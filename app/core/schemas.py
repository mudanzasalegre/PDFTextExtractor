from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ExtractionMode = Literal["faithful", "clean", "academic"]
OCRStrategy = Literal["auto", "never", "always"]
JobStatus = Literal["queued", "running", "completed", "failed"]


class ExtractionOptions(BaseModel):
    """User-facing extraction options.

    Page numbers are 1-based and inclusive.
    """

    start_page: int = Field(1, ge=1)
    end_page: int | None = Field(None, ge=1)
    mode: ExtractionMode = "academic"
    ocr_strategy: OCRStrategy = "auto"
    language: str = Field("spa+eng", min_length=3, max_length=48)
    ocr_dpi: int = Field(250, ge=120, le=450)

    remove_repeated_headers_footers: bool = True
    strip_page_numbers: bool = True
    include_image_captions: bool = True
    mark_captions: bool = False
    preserve_line_breaks: bool = True
    page_breaks: bool = True
    sort_reading_order: bool = True

    # Margins are percentages of page width/height. They are applied only when
    # greater than zero. A user can keep all margins by leaving them at 0.
    top_margin_pct: float = Field(0.0, ge=0.0, le=0.45)
    bottom_margin_pct: float = Field(0.0, ge=0.0, le=0.45)
    left_margin_pct: float = Field(0.0, ge=0.0, le=0.45)
    right_margin_pct: float = Field(0.0, ge=0.0, le=0.45)

    auto_header_footer_zone_pct: float = Field(0.12, ge=0.02, le=0.30)
    repeated_line_min_ratio: float = Field(0.55, ge=0.1, le=1.0)
    native_min_chars: int = Field(80, ge=0, le=5000)
    native_min_words: int = Field(12, ge=0, le=1000)
    generate_searchable_pdf: bool = False

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        cleaned = value.strip().lower().replace(" ", "")
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_+-")
        if not cleaned or any(ch not in allowed for ch in cleaned):
            raise ValueError("language solo puede contener códigos Tesseract, por ejemplo spa o spa+eng")
        return cleaned

    @model_validator(mode="after")
    def validate_page_range(self) -> "ExtractionOptions":
        if self.end_page is not None and self.end_page < self.start_page:
            raise ValueError("end_page debe ser mayor o igual que start_page")
        return self

    def effective_for_mode(self) -> "ExtractionOptions":
        """Return an option copy with mode-specific defaults applied conservatively.

        We avoid changing explicit user values where possible. The web UI sends
        explicit values, while API callers can rely on these defaults.
        """
        if self.mode == "faithful":
            return self.model_copy(update={
                "remove_repeated_headers_footers": False,
                "strip_page_numbers": False,
                "include_image_captions": True,
                "mark_captions": False,
                "preserve_line_breaks": True,
                "top_margin_pct": 0.0,
                "bottom_margin_pct": 0.0,
                "left_margin_pct": 0.0,
                "right_margin_pct": 0.0,
            })
        # clean and academic use the explicit options received from the UI/API.
        # The UI defaults already select a sensible clean/documental setup, but
        # callers can still override each cleanup rule manually.
        return self


class PageReport(BaseModel):
    page_number: int
    source: Literal["native", "ocr", "empty", "error"]
    chars_before_cleanup: int = 0
    chars_after_cleanup: int = 0
    words_before_cleanup: int = 0
    words_after_cleanup: int = 0
    native_score: float = 0.0
    ocr_mean_confidence: float | None = None
    removed_lines: int = 0
    caption_lines: int = 0
    warnings: list[str] = Field(default_factory=list)


class ExtractedLine(BaseModel):
    text: str
    bbox: tuple[float, float, float, float]
    source: Literal["native", "ocr"]
    confidence: float | None = None
    is_caption: bool = False
    removed_reason: str | None = None


class ExtractedPage(BaseModel):
    page_number: int
    width: float
    height: float
    source: Literal["native", "ocr", "empty", "error"]
    text: str
    lines: list[ExtractedLine] = Field(default_factory=list)
    removed_lines: list[ExtractedLine] = Field(default_factory=list)
    report: PageReport


class ExtractionMetadata(BaseModel):
    filename: str
    total_pages: int
    selected_start_page: int
    selected_end_page: int
    options: dict[str, Any]
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ExtractionResult(BaseModel):
    metadata: ExtractionMetadata
    full_text: str
    pages: list[ExtractedPage]
    warnings: list[str] = Field(default_factory=list)


class JobPublic(BaseModel):
    job_id: str
    status: JobStatus
    progress: float = Field(0.0, ge=0.0, le=1.0)
    message: str = ""
    filename: str | None = None
    created_at: str
    updated_at: str
    downloads: dict[str, str] = Field(default_factory=dict)
    preview: str | None = None
    page_reports: list[PageReport] = Field(default_factory=list)
    error: str | None = None


TTSProvider = Literal["auto", "edge", "piper", "espeak"]
AudioFormat = Literal["mp3", "wav"]

EDGE_ES_ES_VOICES = {
    "es-ES-ElviraNeural",
    "es-ES-AlvaroNeural",
    "es-ES-XimenaNeural",
    "es-ES-AbrilNeural",
    "es-ES-ArnauNeural",
    "es-ES-DarioNeural",
    "es-ES-EliasNeural",
    "es-ES-EstrellaNeural",
    "es-ES-IreneNeural",
    "es-ES-LaiaNeural",
    "es-ES-LiaNeural",
    "es-ES-NilNeural",
    "es-ES-SaulNeural",
    "es-ES-TeoNeural",
    "es-ES-TrianaNeural",
    "es-ES-VeraNeural",
}
PIPER_ES_ES_VOICES = {
    "es_ES-sharvard-medium",
    "es_ES-davefx-medium",
    "es_ES-carlfm-x_low",
}
ESPEAK_ES_ES_VOICES = {"es-es", "spanish"}


class TTSOptions(BaseModel):
    """Options for converting a cleaned TXT file to spoken audio."""

    provider: TTSProvider = "auto"
    voice: str = Field("es-ES-ElviraNeural", min_length=3, max_length=96)
    audio_format: AudioFormat = "mp3"
    rate: int = Field(-5, ge=-50, le=50, description="Speaking speed percentage. Negative is slower.")
    pitch_hz: int = Field(0, ge=-100, le=100)
    volume: int = Field(0, ge=-50, le=50)
    bitrate_kbps: int = Field(128, ge=64, le=320)
    chunk_chars: int = Field(1800, ge=300, le=4000)
    normalize_for_narration: bool = True
    remove_page_markers: bool = True
    title: str | None = Field(None, max_length=160)

    @field_validator("voice")
    @classmethod
    def validate_voice_chars(cls, value: str) -> str:
        cleaned = value.strip()
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:-")
        if not cleaned or any(ch not in allowed for ch in cleaned):
            raise ValueError("voice contiene caracteres no permitidos")
        return cleaned

    @model_validator(mode="after")
    def validate_spanish_spain_voice(self) -> "TTSOptions":
        if self.provider in {"edge", "auto"} and self.voice.startswith("es-ES-"):
            if self.voice not in EDGE_ES_ES_VOICES:
                raise ValueError("La voz edge debe ser una voz es-ES permitida")
            return self
        if self.provider == "piper" and self.voice in PIPER_ES_ES_VOICES:
            return self
        if self.provider == "espeak" and self.voice in ESPEAK_ES_ES_VOICES:
            return self
        if self.provider == "auto" and self.voice in PIPER_ES_ES_VOICES:
            return self
        raise ValueError("Solo se admiten voces de español de España: es-ES, es_ES o es-es")


class TTSJobStats(BaseModel):
    provider_used: str | None = None
    voice_used: str | None = None
    audio_format: AudioFormat = "mp3"
    chars: int = 0
    words: int = 0
    chunks: int = 0
    duration_seconds: float | None = None
    warnings: list[str] = Field(default_factory=list)


class TTSPublic(BaseModel):
    job_id: str
    status: JobStatus
    progress: float = Field(0.0, ge=0.0, le=1.0)
    message: str = ""
    filename: str | None = None
    created_at: str
    updated_at: str
    downloads: dict[str, str] = Field(default_factory=dict)
    preview: str | None = None
    stats: TTSJobStats = Field(default_factory=TTSJobStats)
    error: str | None = None
