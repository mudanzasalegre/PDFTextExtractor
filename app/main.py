from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.core.schemas import ExtractionOptions, TTSOptions
from app.core.settings import settings
from app.services.job_store import JobStore
from app.services.tts_job_store import TTSJobStore

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title=settings.app_name, version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

job_store = JobStore(settings.storage_dir, max_workers=settings.max_workers)
tts_job_store = TTSJobStore(settings.storage_dir, max_workers=settings.max_tts_workers)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.get("/api/options")
def options() -> dict[str, object]:
    return {
        "modes": ["faithful", "clean", "academic"],
        "ocr_strategies": ["auto", "never", "always"],
        "default_language": "spa+eng",
        "max_upload_mb": settings.max_upload_mb,
        "exports": ["txt", "md", "docx", "json", "searchable_pdf"],
        "tts": {
            "providers": ["auto", "edge", "piper", "espeak"],
            "default_provider": "auto",
            "default_voice": "es-ES-ElviraNeural",
            "formats": ["mp3", "wav"],
            "max_txt_upload_mb": settings.max_tts_upload_mb,
            "max_chars": settings.max_tts_chars,
            "edge_voices_es_es": [
                "es-ES-ElviraNeural",
                "es-ES-AlvaroNeural",
                "es-ES-XimenaNeural",
                "es-ES-VeraNeural",
                "es-ES-AbrilNeural",
                "es-ES-LaiaNeural",
                "es-ES-LiaNeural",
                "es-ES-TrianaNeural",
                "es-ES-IreneNeural",
                "es-ES-EstrellaNeural",
                "es-ES-NilNeural",
                "es-ES-ArnauNeural",
                "es-ES-DarioNeural",
                "es-ES-EliasNeural",
                "es-ES-SaulNeural",
                "es-ES-TeoNeural",
            ],
            "piper_voices_es_es": ["es_ES-sharvard-medium", "es_ES-davefx-medium", "es_ES-carlfm-x_low"],
            "espeak_voices_es_es": ["es-es"],
        },
    }


@app.post("/api/jobs")
async def create_job(
    file: Annotated[UploadFile, File(description="PDF a procesar")],
    start_page: Annotated[int, Form(ge=1)] = 1,
    end_page: Annotated[int | None, Form(ge=1)] = None,
    mode: Annotated[str, Form()] = "academic",
    ocr_strategy: Annotated[str, Form()] = "auto",
    language: Annotated[str, Form()] = "spa+eng",
    ocr_dpi: Annotated[int, Form(ge=120, le=450)] = 250,
    remove_repeated_headers_footers: Annotated[bool, Form()] = True,
    strip_page_numbers: Annotated[bool, Form()] = True,
    include_image_captions: Annotated[bool, Form()] = True,
    mark_captions: Annotated[bool, Form()] = False,
    preserve_line_breaks: Annotated[bool, Form()] = True,
    page_breaks: Annotated[bool, Form()] = True,
    sort_reading_order: Annotated[bool, Form()] = True,
    top_margin_pct: Annotated[float, Form(ge=0.0, le=0.45)] = 0.0,
    bottom_margin_pct: Annotated[float, Form(ge=0.0, le=0.45)] = 0.0,
    left_margin_pct: Annotated[float, Form(ge=0.0, le=0.45)] = 0.0,
    right_margin_pct: Annotated[float, Form(ge=0.0, le=0.45)] = 0.0,
    auto_header_footer_zone_pct: Annotated[float, Form(ge=0.02, le=0.30)] = 0.12,
    repeated_line_min_ratio: Annotated[float, Form(ge=0.1, le=1.0)] = 0.55,
    native_min_chars: Annotated[int, Form(ge=0, le=5000)] = 80,
    native_min_words: Annotated[int, Form(ge=0, le=1000)] = 12,
    generate_searchable_pdf: Annotated[bool, Form()] = False,
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Debes subir un archivo PDF")
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe tener extensión .pdf")

    try:
        opts = ExtractionOptions(
            start_page=start_page,
            end_page=end_page,
            mode=mode,
            ocr_strategy=ocr_strategy,
            language=language,
            ocr_dpi=ocr_dpi,
            remove_repeated_headers_footers=remove_repeated_headers_footers,
            strip_page_numbers=strip_page_numbers,
            include_image_captions=include_image_captions,
            mark_captions=mark_captions,
            preserve_line_breaks=preserve_line_breaks,
            page_breaks=page_breaks,
            sort_reading_order=sort_reading_order,
            top_margin_pct=top_margin_pct,
            bottom_margin_pct=bottom_margin_pct,
            left_margin_pct=left_margin_pct,
            right_margin_pct=right_margin_pct,
            auto_header_footer_zone_pct=auto_header_footer_zone_pct,
            repeated_line_min_ratio=repeated_line_min_ratio,
            native_min_chars=native_min_chars,
            native_min_words=native_min_words,
            generate_searchable_pdf=generate_searchable_pdf,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    work_dir = settings.storage_dir / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    input_pdf = work_dir / "entrada.pdf"
    try:
        await _save_upload(file, input_pdf, settings.max_upload_mb)
        _validate_pdf_magic(input_pdf)
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"No se pudo guardar el PDF: {exc}") from exc

    record = job_store.create_job(input_pdf, file.filename, opts)
    job_store.submit(record.job_id)
    return JSONResponse(status_code=202, content=record.to_public().model_dump())


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    public = job_store.public(job_id)
    if not public:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return public


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    deleted = job_store.delete(job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return {"deleted": True}


@app.get("/api/jobs/{job_id}/download/{fmt}")
def download(job_id: str, fmt: str):
    item = job_store.file_for_download(job_id, fmt)
    if not item:
        raise HTTPException(status_code=404, detail="Archivo no disponible")
    path, media_type = item
    suffix = path.suffix or f".{fmt}"
    filename = f"extraccion_{job_id[:8]}{suffix}"
    if fmt == "searchable_pdf":
        filename = f"pdf_buscable_{job_id[:8]}.pdf"
    return FileResponse(path, media_type=media_type, filename=filename)


@app.post("/api/tts/jobs")
async def create_tts_job(
    file: Annotated[UploadFile, File(description="TXT a convertir a voz")],
    provider: Annotated[str, Form()] = "auto",
    voice: Annotated[str, Form()] = "es-ES-ElviraNeural",
    audio_format: Annotated[str, Form()] = "mp3",
    rate: Annotated[int, Form(ge=-50, le=50)] = -5,
    pitch_hz: Annotated[int, Form(ge=-100, le=100)] = 0,
    volume: Annotated[int, Form(ge=-50, le=50)] = 0,
    bitrate_kbps: Annotated[int, Form(ge=64, le=320)] = 128,
    chunk_chars: Annotated[int, Form(ge=300, le=4000)] = 1800,
    normalize_for_narration: Annotated[bool, Form()] = True,
    remove_page_markers: Annotated[bool, Form()] = True,
    title: Annotated[str | None, Form(max_length=160)] = None,
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Debes subir un archivo TXT")
    if not file.filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="El archivo debe tener extensión .txt")

    try:
        opts = TTSOptions(
            provider=provider,
            voice=voice,
            audio_format=audio_format,
            rate=rate,
            pitch_hz=pitch_hz,
            volume=volume,
            bitrate_kbps=bitrate_kbps,
            chunk_chars=chunk_chars,
            normalize_for_narration=normalize_for_narration,
            remove_page_markers=remove_page_markers,
            title=title.strip() if title else None,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    work_dir = settings.storage_dir / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    input_txt = work_dir / "entrada.txt"
    try:
        await _save_upload(file, input_txt, settings.max_tts_upload_mb)
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"No se pudo guardar el TXT: {exc}") from exc

    record = tts_job_store.create_job(input_txt, file.filename, opts)
    tts_job_store.submit(record.job_id)
    return JSONResponse(status_code=202, content=record.to_public().model_dump())


@app.get("/api/tts/jobs/{job_id}")
def get_tts_job(job_id: str):
    public = tts_job_store.public(job_id)
    if not public:
        raise HTTPException(status_code=404, detail="Job TTS no encontrado")
    return public


@app.delete("/api/tts/jobs/{job_id}")
def delete_tts_job(job_id: str):
    deleted = tts_job_store.delete(job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job TTS no encontrado")
    return {"deleted": True}


@app.get("/api/tts/jobs/{job_id}/download/{item}")
def download_tts(job_id: str, item: str):
    download_item = tts_job_store.file_for_download(job_id, item)
    if not download_item:
        raise HTTPException(status_code=404, detail="Archivo no disponible")
    path, media_type = download_item
    if item == "audio":
        filename = f"audio_{job_id[:8]}{path.suffix}"
    else:
        filename = f"audio_metadata_{job_id[:8]}.json"
    return FileResponse(path, media_type=media_type, filename=filename)


async def _save_upload(file: UploadFile, output_path: Path, max_upload_mb: int) -> None:
    max_bytes = max_upload_mb * 1024 * 1024
    size = 0
    with output_path.open("wb") as buffer:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(status_code=413, detail=f"El PDF supera el límite de {max_upload_mb} MB")
            buffer.write(chunk)
    if size == 0:
        raise HTTPException(status_code=400, detail="El archivo está vacío")


def _validate_pdf_magic(path: Path) -> None:
    with path.open("rb") as fh:
        magic = fh.read(5)
    if magic != b"%PDF-":
        raise HTTPException(status_code=400, detail="El archivo no parece ser un PDF válido")
