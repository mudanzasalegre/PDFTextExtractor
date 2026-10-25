from __future__ import annotations

import os
from pathlib import Path


class Settings:
    app_name: str = os.getenv("APP_NAME", "Extractor PDF y Audio Español")
    storage_dir: Path = Path(os.getenv("STORAGE_DIR", "storage")).resolve()
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "300"))
    max_tts_upload_mb: int = int(os.getenv("MAX_TTS_UPLOAD_MB", "50"))
    max_tts_chars: int = int(os.getenv("MAX_TTS_CHARS", "750000"))
    max_workers: int = int(os.getenv("MAX_WORKERS", "2"))
    max_tts_workers: int = int(os.getenv("MAX_TTS_WORKERS", "1"))
    piper_models_dir: Path = Path(os.getenv("PIPER_MODELS_DIR", "/app/models/piper")).resolve()
    piper_default_voice: str = os.getenv("PIPER_DEFAULT_VOICE", "es_ES-sharvard-medium")
    cors_allow_origins: list[str] = [origin.strip() for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if origin.strip()]


settings = Settings()
