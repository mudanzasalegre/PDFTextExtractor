from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.schemas import TTSOptions
from app.core.tts import prepare_text_for_narration, split_text_for_tts


def test_prepare_text_for_narration_removes_page_markers_and_repairs_hyphenation() -> None:
    raw = """--- Página 5 ---\nLa informa-\nción aparece en dos líneas.\n\nOtra línea\ncontinuada."""

    text = prepare_text_for_narration(raw, TTSOptions())

    assert "Página 5" not in text
    assert "información" in text
    assert "Otra línea continuada" in text


def test_split_text_for_tts_respects_max_chars() -> None:
    text = "\n\n".join(["Esta es una frase de prueba para generar audio." for _ in range(80)])

    chunks = split_text_for_tts(text, max_chars=320)

    assert len(chunks) > 1
    assert all(len(chunk) <= 320 for chunk in chunks)


def test_tts_options_only_allow_spanish_spain_voices() -> None:
    assert TTSOptions(provider="edge", voice="es-ES-ElviraNeural").voice == "es-ES-ElviraNeural"
    assert TTSOptions(provider="piper", voice="es_ES-sharvard-medium").voice == "es_ES-sharvard-medium"

    with pytest.raises(ValidationError):
        TTSOptions(provider="edge", voice="es-MX-DaliaNeural")
