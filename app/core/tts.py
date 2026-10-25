from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Literal

from app.core.schemas import TTSOptions
from app.core.settings import settings

ProgressCallback = Callable[[float, str], None]

WORD_RE = re.compile(r"[\wÁÉÍÓÚÜÑáéíóúüñ]+", re.UNICODE)
PAGE_MARKER_RE = re.compile(
    r"^\s*(?:[-–—=]{2,}\s*)?(?:p[áa]gina|page|pag\.?|p\.)\s*\d{1,6}\s*(?:[-–—=]{2,})?\s*$",
    re.IGNORECASE,
)
EXTRACTION_PAGE_MARKER_RE = re.compile(r"^\s*[-–—]{2,}\s*(?:P[áa]gina|Page)\s+\d{1,6}\s*[-–—]{2,}\s*$", re.IGNORECASE)
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MULTISPACE_RE = re.compile(r"[ \t]{2,}")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")

EDGE_DEFAULT_VOICE = "es-ES-ElviraNeural"
PIPER_DEFAULT_VOICE = settings.piper_default_voice
ESPEAK_DEFAULT_VOICE = "es-es"

PIPER_MODEL_FILES = {
    "es_ES-sharvard-medium": "es_ES-sharvard-medium.onnx",
    "es_ES-davefx-medium": "es_ES-davefx-medium.onnx",
    "es_ES-carlfm-x_low": "es_ES-carlfm-x_low.onnx",
}


class TTSGenerationError(RuntimeError):
    """Raised when one TTS provider cannot generate the requested audio."""


@dataclass(slots=True)
class TTSOutput:
    audio_path: Path
    metadata_path: Path
    provider_used: str
    voice_used: str
    audio_format: Literal["mp3", "wav"]
    chars: int
    words: int
    chunks: int
    duration_seconds: float | None = None
    warnings: list[str] = field(default_factory=list)
    preview: str = ""


def synthesize_txt_file(
    txt_path: str | Path,
    output_dir: str | Path,
    options: TTSOptions,
    original_filename: str | None = None,
    progress_cb: ProgressCallback | None = None,
) -> TTSOutput:
    """Convert a TXT file into speech audio using a Spanish-Spain voice.

    The provider can be:
    - auto: Edge neural first, then Piper local, then eSpeak fallback.
    - edge: high-quality online Microsoft Edge voices through edge-tts.
    - piper: local neural model, if the model files are installed.
    - espeak: local deterministic fallback, less natural but robust.
    """

    txt_path = Path(txt_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def progress(value: float, message: str) -> None:
        if progress_cb:
            progress_cb(max(0.0, min(1.0, value)), message)

    progress(0.02, "Leyendo TXT")
    raw_text = read_text_file(txt_path)
    if not raw_text.strip():
        raise ValueError("El archivo TXT no contiene texto legible")
    if len(raw_text) > settings.max_tts_chars:
        raise ValueError(f"El TXT supera el límite de {settings.max_tts_chars:,} caracteres".replace(",", "."))

    progress(0.08, "Preparando texto para narración")
    text = prepare_text_for_narration(raw_text, options)
    if not text.strip():
        raise ValueError("El texto quedó vacío después de la limpieza para narración")

    chunks = split_text_for_tts(text, max_chars=options.chunk_chars)
    if not chunks:
        raise ValueError("No se pudo dividir el texto en fragmentos de audio")

    providers = _provider_order(options.provider)
    warnings: list[str] = []
    last_error: Exception | None = None

    for provider in providers:
        try:
            progress(0.12, f"Sintetizando audio con {provider}")
            with tempfile.TemporaryDirectory(dir=output_dir) as temp_name:
                temp_dir = Path(temp_name)
                audio_path, provider_voice = _synthesize_with_provider(provider, chunks, temp_dir, output_dir, options, progress)
            duration = probe_audio_duration(audio_path)
            metadata = {
                "input_filename": original_filename or txt_path.name,
                "provider_requested": options.provider,
                "provider_used": provider,
                "voice_requested": options.voice,
                "voice_used": provider_voice,
                "audio_format": options.audio_format,
                "chars": len(text),
                "words": len(WORD_RE.findall(text)),
                "chunks": len(chunks),
                "duration_seconds": duration,
                "options": options.model_dump(),
                "warnings": warnings,
            }
            metadata_path = output_dir / "audio_metadata.json"
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            progress(1.0, "Audio completado")
            return TTSOutput(
                audio_path=audio_path,
                metadata_path=metadata_path,
                provider_used=provider,
                voice_used=provider_voice,
                audio_format=options.audio_format,
                chars=len(text),
                words=len(WORD_RE.findall(text)),
                chunks=len(chunks),
                duration_seconds=duration,
                warnings=warnings,
                preview=text[:8000],
            )
        except Exception as exc:
            last_error = exc
            if options.provider != "auto":
                raise
            warnings.append(f"{provider} no pudo generar audio: {_short_error(str(exc))}")
            progress(0.12, f"{provider} falló; probando otro motor")

    detail = _short_error(str(last_error)) if last_error else "sin detalle"
    raise TTSGenerationError(f"No se pudo generar el audio con ningún motor disponible: {detail}")


def read_text_file(path: str | Path) -> str:
    path = Path(path)
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def prepare_text_for_narration(text: str, options: TTSOptions | None = None) -> str:
    """Normalize a cleaned extraction TXT into a form that TTS engines read well."""
    remove_page_markers = True if options is None else options.remove_page_markers
    normalize = True if options is None else options.normalize_for_narration

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = CONTROL_CHARS_RE.sub("", text)
    text = text.replace("\u00a0", " ")

    if remove_page_markers:
        kept_lines: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if PAGE_MARKER_RE.match(stripped) or EXTRACTION_PAGE_MARKER_RE.match(stripped):
                continue
            kept_lines.append(line)
        text = "\n".join(kept_lines)

    # Repair common OCR/PDF line-wrap hyphenation: "informa-\nción" -> "información".
    text = re.sub(r"(?<=[A-Za-zÁÉÍÓÚÜÑáéíóúüñ])-\n(?=[a-záéíóúüñ])", "", text)

    if not normalize:
        lines = [MULTISPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line).strip()

    paragraphs = _paragraphs(text)
    normalized: list[str] = []
    for paragraph in paragraphs:
        lines = [MULTISPACE_RE.sub(" ", line).strip() for line in paragraph.splitlines()]
        joined = " ".join(line for line in lines if line)
        joined = MULTISPACE_RE.sub(" ", joined).strip()
        if joined:
            normalized.append(joined)
    return "\n\n".join(normalized).strip()


def split_text_for_tts(text: str, max_chars: int = 1800) -> list[str]:
    """Split text into chunks that are safe for web/CLI TTS engines."""
    if max_chars < 300:
        raise ValueError("max_chars debe ser al menos 300")

    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        current = current.strip()
        if current:
            chunks.append(current)
            current = ""

    for paragraph in _paragraphs(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        pieces = _split_long_piece(paragraph, max_chars)
        for piece in pieces:
            candidate = f"{current}\n\n{piece}".strip() if current else piece
            if len(candidate) <= max_chars:
                current = candidate
            else:
                flush()
                if len(piece) <= max_chars:
                    current = piece
                else:
                    for hard_piece in _hard_wrap(piece, max_chars):
                        candidate = f"{current} {hard_piece}".strip() if current else hard_piece
                        if len(candidate) <= max_chars:
                            current = candidate
                        else:
                            flush()
                            current = hard_piece
    flush()
    return chunks


def _split_long_piece(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    sentences = SENTENCE_SPLIT_RE.split(text)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                pieces.append(current)
            if len(sentence) <= max_chars:
                current = sentence
            else:
                pieces.extend(_hard_wrap(sentence, max_chars))
                current = ""
    if current:
        pieces.append(current)
    return pieces


def _hard_wrap(text: str, max_chars: int) -> list[str]:
    words = text.split()
    pieces: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                pieces.append(current)
            current = word[:max_chars]
            remainder = word[max_chars:]
            while remainder:
                pieces.append(current)
                current = remainder[:max_chars]
                remainder = remainder[max_chars:]
    if current:
        pieces.append(current)
    return pieces


def _paragraphs(text: str) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def _provider_order(provider: str) -> list[str]:
    if provider == "auto":
        return ["edge", "piper", "espeak"]
    return [provider]


def _synthesize_with_provider(
    provider: str,
    chunks: list[str],
    temp_dir: Path,
    output_dir: Path,
    options: TTSOptions,
    progress: ProgressCallback,
) -> tuple[Path, str]:
    if provider == "edge":
        voice = options.voice if options.voice.startswith("es-ES-") else EDGE_DEFAULT_VOICE
        chunk_paths = _synthesize_edge(chunks, temp_dir, voice, options, progress)
    elif provider == "piper":
        voice = options.voice if options.voice.startswith("es_ES-") else PIPER_DEFAULT_VOICE
        chunk_paths = _synthesize_piper(chunks, temp_dir, voice, options, progress)
    elif provider == "espeak":
        voice = options.voice if options.voice in {"es-es", "spanish"} else ESPEAK_DEFAULT_VOICE
        chunk_paths = _synthesize_espeak(chunks, temp_dir, voice, options, progress)
    else:  # pragma: no cover - guarded by Pydantic
        raise TTSGenerationError(f"Proveedor TTS no soportado: {provider}")

    final_path = output_dir / f"resultado_audio.{options.audio_format}"
    _concat_audio(chunk_paths, final_path, options.audio_format, options.bitrate_kbps)
    return final_path, voice


def _synthesize_edge(chunks: list[str], temp_dir: Path, voice: str, options: TTSOptions, progress: ProgressCallback) -> list[Path]:
    binary = shutil.which("edge-tts")
    if not binary:
        raise TTSGenerationError("edge-tts no está instalado")

    paths: list[Path] = []
    for idx, chunk in enumerate(chunks, start=1):
        output = temp_dir / f"edge_{idx:05d}.mp3"
        cmd = [
            binary,
            f"--voice={voice}",
            f"--rate={_format_percent(options.rate)}",
            f"--volume={_format_percent(options.volume)}",
            f"--pitch={_format_pitch(options.pitch_hz)}",
            "--text",
            chunk,
            "--write-media",
            str(output),
        ]
        _run(cmd, timeout=600, error_label="edge-tts")
        if not output.exists() or output.stat().st_size == 0:
            raise TTSGenerationError("edge-tts no generó audio")
        paths.append(output)
        progress(0.12 + 0.76 * idx / max(1, len(chunks)), f"Audio edge: fragmento {idx}/{len(chunks)}")
    return paths


def _synthesize_piper(chunks: list[str], temp_dir: Path, voice: str, options: TTSOptions, progress: ProgressCallback) -> list[Path]:
    binary = shutil.which("piper")
    if not binary:
        raise TTSGenerationError("piper no está instalado")
    model_file = PIPER_MODEL_FILES.get(voice)
    if not model_file:
        raise TTSGenerationError(f"Voz Piper no configurada: {voice}")
    model_path = settings.piper_models_dir / model_file
    config_path = Path(str(model_path) + ".json")
    if not model_path.exists() or not config_path.exists():
        raise TTSGenerationError(f"No se encontró el modelo Piper {voice} en {settings.piper_models_dir}")

    paths: list[Path] = []
    for idx, chunk in enumerate(chunks, start=1):
        output = temp_dir / f"piper_{idx:05d}.wav"
        cmd = [binary, "--model", str(model_path), "--output_file", str(output)]
        _run(cmd, input_text=chunk, timeout=600, error_label="piper")
        if not output.exists() or output.stat().st_size == 0:
            raise TTSGenerationError("piper no generó audio")
        paths.append(output)
        progress(0.12 + 0.76 * idx / max(1, len(chunks)), f"Audio Piper: fragmento {idx}/{len(chunks)}")
    return paths


def _synthesize_espeak(chunks: list[str], temp_dir: Path, voice: str, options: TTSOptions, progress: ProgressCallback) -> list[Path]:
    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    if not binary:
        raise TTSGenerationError("espeak-ng no está instalado")
    speed = int(round(165 * (1 + options.rate / 100)))
    speed = max(80, min(260, speed))
    paths: list[Path] = []
    for idx, chunk in enumerate(chunks, start=1):
        output = temp_dir / f"espeak_{idx:05d}.wav"
        cmd = [binary, "-v", voice, "-s", str(speed), "-w", str(output)]
        _run(cmd, input_text=chunk, timeout=300, error_label="espeak-ng")
        if not output.exists() or output.stat().st_size == 0:
            raise TTSGenerationError("espeak-ng no generó audio")
        paths.append(output)
        progress(0.12 + 0.76 * idx / max(1, len(chunks)), f"Audio eSpeak: fragmento {idx}/{len(chunks)}")
    return paths


def _concat_audio(chunk_paths: list[Path], output_path: Path, audio_format: str, bitrate_kbps: int) -> None:
    if not chunk_paths:
        raise TTSGenerationError("No hay fragmentos de audio que unir")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if len(chunk_paths) == 1 and chunk_paths[0].suffix.lower().lstrip(".") == audio_format:
        shutil.copyfile(chunk_paths[0], output_path)
        return

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TTSGenerationError("ffmpeg no está instalado; es necesario para unir o convertir audio")

    list_path = output_path.parent / "audio_chunks.txt"
    list_path.write_text("".join(f"file '{_ffmpeg_escape(path)}'\n" for path in chunk_paths), encoding="utf-8")

    if audio_format == "mp3":
        # First try stream-copy for homogeneous MP3 chunks; if it fails, re-encode.
        if all(path.suffix.lower() == ".mp3" for path in chunk_paths):
            cmd_copy = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(output_path)]
            completed = subprocess.run(cmd_copy, capture_output=True, text=True, check=False)
            if completed.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                return
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-b:a",
            f"{bitrate_kbps}k",
            str(output_path),
        ]
    elif audio_format == "wav":
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "22050",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    else:  # pragma: no cover - guarded by Pydantic
        raise TTSGenerationError(f"Formato de audio no soportado: {audio_format}")
    _run(cmd, timeout=900, error_label="ffmpeg")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise TTSGenerationError("ffmpeg no generó el archivo final")


def _ffmpeg_escape(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def probe_audio_duration(path: str | Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            return None
        value = float((completed.stdout or "").strip())
        return round(value, 2)
    except Exception:
        return None


def _run(cmd: list[str], input_text: str | None = None, timeout: int = 300, error_label: str = "comando") -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TTSGenerationError(f"{error_label} superó el tiempo máximo") from exc
    except Exception as exc:
        raise TTSGenerationError(f"No se pudo ejecutar {error_label}: {exc}") from exc

    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip().replace("\n", " ")
        raise TTSGenerationError(f"{error_label} falló: {_short_error(details)}")
    return completed


def _format_percent(value: int) -> str:
    return f"{value:+d}%"


def _format_pitch(value: int) -> str:
    return f"{value:+d}Hz"


def _short_error(text: str, max_len: int = 500) -> str:
    text = text.strip()
    return text[:max_len] + ("…" if len(text) > max_len else "")
