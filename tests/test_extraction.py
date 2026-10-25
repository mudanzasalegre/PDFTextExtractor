from __future__ import annotations

from pathlib import Path

import fitz

from app.core.pdf_extractor import extract_document
from app.core.schemas import ExtractionOptions


def _make_sample_pdf(path: Path, pages: int = 3) -> None:
    doc = fitz.open()
    for number in range(1, pages + 1):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 36), "MI LIBRO", fontsize=10)
        page.insert_text((72, 110), f"Capítulo de prueba página {number}", fontsize=18)
        page.insert_text((72, 150), f"Este es el cuerpo principal de la página {number}.", fontsize=12)
        page.insert_text((72, 170), "Contiene texto íntegro en español con acentos: acción, información, niño.", fontsize=12)
        page.insert_text((300, 812), str(number), fontsize=10)
    doc.save(path)
    doc.close()


def test_extracts_requested_range_and_removes_repeated_header_footer(tmp_path: Path) -> None:
    pdf = tmp_path / "sample.pdf"
    _make_sample_pdf(pdf, pages=4)

    result = extract_document(
        pdf,
        ExtractionOptions(
            start_page=2,
            end_page=3,
            mode="clean",
            ocr_strategy="never",
            preserve_line_breaks=True,
        ),
        filename="sample.pdf",
    )

    assert result.metadata.selected_start_page == 2
    assert result.metadata.selected_end_page == 3
    assert "cuerpo principal de la página 2" in result.full_text
    assert "cuerpo principal de la página 3" in result.full_text
    assert "cuerpo principal de la página 1" not in result.full_text
    assert "MI LIBRO" not in result.full_text
    assert "\n2\n" not in result.full_text


def test_faithful_mode_keeps_repeated_header(tmp_path: Path) -> None:
    pdf = tmp_path / "sample.pdf"
    _make_sample_pdf(pdf, pages=2)

    result = extract_document(
        pdf,
        ExtractionOptions(start_page=1, end_page=1, mode="faithful", ocr_strategy="never"),
        filename="sample.pdf",
    )

    assert "MI LIBRO" in result.full_text
    assert "--- Página 1 ---" in result.full_text
