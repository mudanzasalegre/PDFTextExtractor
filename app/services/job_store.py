from __future__ import annotations

import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from app.core.exporters import create_searchable_pdf, write_all_exports
from app.core.pdf_extractor import extract_document
from app.core.schemas import ExtractionOptions, JobPublic, JobStatus, PageReport


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobRecord:
    job_id: str
    status: JobStatus
    progress: float
    message: str
    created_at: str
    updated_at: str
    work_dir: Path
    input_pdf: Path
    original_filename: str
    options: ExtractionOptions
    downloads: dict[str, str] = field(default_factory=dict)
    preview: str | None = None
    page_reports: list[PageReport] = field(default_factory=list)
    error: str | None = None

    def to_public(self) -> JobPublic:
        return JobPublic(
            job_id=self.job_id,
            status=self.status,
            progress=self.progress,
            message=self.message,
            filename=self.original_filename,
            created_at=self.created_at,
            updated_at=self.updated_at,
            downloads=self.downloads,
            preview=self.preview,
            page_reports=self.page_reports,
            error=self.error,
        )


class JobStore:
    def __init__(self, storage_dir: str | Path, max_workers: int = 2):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.RLock()

    def create_job(self, input_pdf: Path, original_filename: str, options: ExtractionOptions) -> JobRecord:
        job_id = uuid.uuid4().hex
        work_dir = input_pdf.parent
        now = utc_now()
        record = JobRecord(
            job_id=job_id,
            status="queued",
            progress=0.0,
            message="En cola",
            created_at=now,
            updated_at=now,
            work_dir=work_dir,
            input_pdf=input_pdf,
            original_filename=original_filename,
            options=options,
        )
        with self._lock:
            self._jobs[job_id] = record
        return record

    def submit(self, job_id: str) -> None:
        self.executor.submit(self._run_job, job_id)

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def public(self, job_id: str) -> JobPublic | None:
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
        self._update(job_id, status="running", progress=0.01, message="Iniciando extracción")

        def progress_cb(progress: float, message: str) -> None:
            # Reserve the final 10% for exports.
            self._update(job_id, progress=min(0.90, progress * 0.90), message=message)

        try:
            result = extract_document(
                record.input_pdf,
                record.options,
                filename=record.original_filename,
                progress_cb=progress_cb,
            )
            self._update(job_id, progress=0.92, message="Generando archivos de salida")
            exports = write_all_exports(result, record.work_dir / "exports")
            downloads = {fmt: f"/api/jobs/{job_id}/download/{fmt}" for fmt in exports}

            warnings = list(result.warnings)
            if record.options.generate_searchable_pdf:
                self._update(job_id, progress=0.95, message="Generando PDF buscable con OCRmyPDF")
                searchable_path = record.work_dir / "exports" / "pdf_buscable.pdf"
                ok, warning = create_searchable_pdf(record.input_pdf, searchable_path, record.options.language)
                if ok:
                    downloads["searchable_pdf"] = f"/api/jobs/{job_id}/download/searchable_pdf"
                elif warning:
                    warnings.append(warning)

            if warnings:
                # Persist warning-enriched JSON/TXT is not strictly necessary; page reports expose warnings.
                pass

            preview = result.full_text[:8000]
            self._update(
                job_id,
                status="completed",
                progress=1.0,
                message="Extracción completada",
                downloads=downloads,
                preview=preview,
                page_reports=[page.report for page in result.pages],
            )
        except Exception as exc:
            self._update(
                job_id,
                status="failed",
                progress=1.0,
                message="La extracción falló",
                error=str(exc),
            )

    def file_for_download(self, job_id: str, fmt: str) -> tuple[Path, str] | None:
        record = self.get(job_id)
        if not record or record.status != "completed":
            return None
        mapping = {
            "txt": (record.work_dir / "exports" / "resultado.txt", "text/plain; charset=utf-8"),
            "md": (record.work_dir / "exports" / "resultado.md", "text/markdown; charset=utf-8"),
            "json": (record.work_dir / "exports" / "resultado.json", "application/json"),
            "docx": (record.work_dir / "exports" / "resultado.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            "searchable_pdf": (record.work_dir / "exports" / "pdf_buscable.pdf", "application/pdf"),
        }
        item = mapping.get(fmt)
        if not item:
            return None
        path, media_type = item
        if not path.exists():
            return None
        return path, media_type
