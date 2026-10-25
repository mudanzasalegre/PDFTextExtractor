from __future__ import annotations

import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.core.schemas import JobStatus, TTSJobStats, TTSOptions, TTSPublic
from app.core.tts import synthesize_txt_file


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TTSJobRecord:
    job_id: str
    status: JobStatus
    progress: float
    message: str
    created_at: str
    updated_at: str
    work_dir: Path
    input_txt: Path
    original_filename: str
    options: TTSOptions
    downloads: dict[str, str] = field(default_factory=dict)
    preview: str | None = None
    stats: TTSJobStats = field(default_factory=TTSJobStats)
    error: str | None = None

    def to_public(self) -> TTSPublic:
        return TTSPublic(
            job_id=self.job_id,
            status=self.status,
            progress=self.progress,
            message=self.message,
            filename=self.original_filename,
            created_at=self.created_at,
            updated_at=self.updated_at,
            downloads=self.downloads,
            preview=self.preview,
            stats=self.stats,
            error=self.error,
        )


class TTSJobStore:
    def __init__(self, storage_dir: str | Path, max_workers: int = 1):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[str, TTSJobRecord] = {}
        self._lock = threading.RLock()

    def create_job(self, input_txt: Path, original_filename: str, options: TTSOptions) -> TTSJobRecord:
        job_id = uuid.uuid4().hex
        work_dir = input_txt.parent
        now = utc_now()
        record = TTSJobRecord(
            job_id=job_id,
            status="queued",
            progress=0.0,
            message="En cola",
            created_at=now,
            updated_at=now,
            work_dir=work_dir,
            input_txt=input_txt,
            original_filename=original_filename,
            options=options,
        )
        with self._lock:
            self._jobs[job_id] = record
        return record

    def submit(self, job_id: str) -> None:
        self.executor.submit(self._run_job, job_id)

    def get(self, job_id: str) -> TTSJobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def public(self, job_id: str) -> TTSPublic | None:
        record = self.get(job_id)
        return record.to_public() if record else None

    def delete(self, job_id: str) -> bool:
        with self._lock:
            record = self._jobs.pop(job_id, None)
        if not record:
            return False
        shutil.rmtree(record.work_dir, ignore_errors=True)
        return True

    def _update(self, job_id: str, **changes: object) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                return
            for key, value in changes.items():
                setattr(record, key, value)
            record.updated_at = utc_now()

    def _run_job(self, job_id: str) -> None:
        record = self.get(job_id)
        if not record:
            return
        self._update(job_id, status="running", progress=0.01, message="Iniciando conversión a voz")

        def progress_cb(progress: float, message: str) -> None:
            self._update(job_id, progress=max(0.01, min(0.98, progress)), message=message)

        try:
            output = synthesize_txt_file(
                record.input_txt,
                record.work_dir / "tts_exports",
                record.options,
                original_filename=record.original_filename,
                progress_cb=progress_cb,
            )
            downloads = {
                "audio": f"/api/tts/jobs/{job_id}/download/audio",
                "metadata": f"/api/tts/jobs/{job_id}/download/metadata",
            }
            stats = TTSJobStats(
                provider_used=output.provider_used,
                voice_used=output.voice_used,
                audio_format=output.audio_format,
                chars=output.chars,
                words=output.words,
                chunks=output.chunks,
                duration_seconds=output.duration_seconds,
                warnings=output.warnings,
            )
            self._update(
                job_id,
                status="completed",
                progress=1.0,
                message="Audio completado",
                downloads=downloads,
                preview=output.preview,
                stats=stats,
            )
        except Exception as exc:
            self._update(
                job_id,
                status="failed",
                progress=1.0,
                message="La conversión a voz falló",
                error=str(exc),
            )

    def file_for_download(self, job_id: str, item: str) -> tuple[Path, str] | None:
        record = self.get(job_id)
        if not record or record.status != "completed":
            return None
        exports = record.work_dir / "tts_exports"
        mapping = {
            "audio": (exports / f"resultado_audio.{record.options.audio_format}", _media_type(record.options.audio_format)),
            "metadata": (exports / "audio_metadata.json", "application/json"),
        }
        selected = mapping.get(item)
        if not selected:
            return None
        path, media_type = selected
        if not path.exists():
            return None
        return path, media_type


def _media_type(fmt: str) -> str:
    if fmt == "mp3":
        return "audio/mpeg"
    if fmt == "wav":
        return "audio/wav"
    return "application/octet-stream"
