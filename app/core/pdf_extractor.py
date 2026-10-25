from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Literal

import fitz  # PyMuPDF
from PIL import Image
import pytesseract
from pytesseract import Output

from app.core.schemas import (
    ExtractedLine,
    ExtractedPage,
    ExtractionMetadata,
    ExtractionOptions,
    ExtractionResult,
    PageReport,
)

ProgressCallback = Callable[[float, str], None]

CAPTION_RE = re.compile(
    r"^\s*(fig(?:\.|ura)?|imagen|ilustraci[oó]n|gr[aá]fico|tabla|cuadro|mapa|foto|lámina)"
    r"\s*(?:n[úu]m\.?\s*)?(?:[0-9ivxlcdm]+)?[\.\-:\)]?\s+",
    re.IGNORECASE,
)
PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:[-–—]\s*)?(?:(?:p[áa]g(?:ina)?\.?|page)\s*)?(?:\d{1,5}|[ivxlcdm]{1,12})\s*(?:[-–—])?\s*$",
    re.IGNORECASE,
)
WHITESPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[\wÁÉÍÓÚÜÑáéíóúüñ]+", re.UNICODE)


@dataclass(slots=True)
class InternalLine:
    page_number: int
    text: str
    bbox: tuple[float, float, float, float]
    source: Literal["native", "ocr"]
    block_id: int = 0
    line_id: int = 0
    confidence: float | None = None
    font_size: float | None = None
    is_caption: bool = False
    removed_reason: str | None = None

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def height(self) -> float:
        return max(0.1, self.y1 - self.y0)


@dataclass(slots=True)
class RawPage:
    page_number: int
    width: float
    height: float
    source: Literal["native", "ocr", "empty", "error"]
    native_score: float
    lines: list[InternalLine] = field(default_factory=list)
    image_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ocr_mean_confidence: float | None = None


def extract_document(
    pdf_path: str | Path,
    options: ExtractionOptions,
    filename: str | None = None,
    progress_cb: ProgressCallback | None = None,
) -> ExtractionResult:
    """Extract text from a PDF range using native extraction plus OCR fallback."""

    pdf_path = Path(pdf_path)
    opts = options.effective_for_mode()
    warnings: list[str] = []

    def progress(value: float, message: str) -> None:
        if progress_cb:
            progress_cb(max(0.0, min(1.0, value)), message)

    progress(0.01, "Abriendo PDF")
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:  # pragma: no cover - depends on corrupted input
        raise ValueError(f"No se pudo abrir el PDF: {exc}") from exc

    try:
        total_pages = doc.page_count
        if total_pages <= 0:
            raise ValueError("El PDF no contiene páginas")
        start_page = opts.start_page
        end_page = opts.end_page or total_pages
        if start_page > total_pages:
            raise ValueError(f"start_page={start_page} supera el total de páginas ({total_pages})")
        if end_page > total_pages:
            raise ValueError(f"end_page={end_page} supera el total de páginas ({total_pages})")
        if end_page < start_page:
            raise ValueError("El rango de páginas no es válido")

        selected_count = end_page - start_page + 1
        raw_pages: list[RawPage] = []

        for idx, page_index in enumerate(range(start_page - 1, end_page), start=1):
            page = doc[page_index]
            page_number = page_index + 1
            progress(0.05 + 0.60 * ((idx - 1) / max(1, selected_count)), f"Analizando página {page_number}")

            raw = _extract_page(page, page_number, opts)
            raw_pages.append(raw)
            progress(0.05 + 0.60 * (idx / max(1, selected_count)), f"Página {page_number} procesada")

        progress(0.72, "Detectando cabeceras, pies y ruido repetido")
        repeated_norms = _detect_repeated_header_footer_lines(raw_pages, opts)

        progress(0.80, "Reconstruyendo orden de lectura")
        processed_pages = [_cleanup_and_render_page(raw, opts, repeated_norms) for raw in raw_pages]

        full_text = _compose_full_text(processed_pages, opts)
        metadata = ExtractionMetadata(
            filename=filename or pdf_path.name,
            total_pages=total_pages,
            selected_start_page=start_page,
            selected_end_page=end_page,
            options=opts.model_dump(),
        )
        progress(0.98, "Resultado preparado")
        return ExtractionResult(metadata=metadata, full_text=full_text, pages=processed_pages, warnings=warnings)
    finally:
        doc.close()


def _extract_page(page: fitz.Page, page_number: int, opts: ExtractionOptions) -> RawPage:
    native_text = page.get_text("text", sort=True) or ""
    native_score = _score_native_text(native_text)
    has_usable_native = _has_usable_native_text(native_text, native_score, opts)

    if opts.ocr_strategy == "always":
        return _extract_page_ocr(page, page_number, opts, native_score)
    if opts.ocr_strategy == "never" or has_usable_native:
        raw = _extract_page_native(page, page_number, native_score)
        if not raw.lines and opts.ocr_strategy == "never":
            raw.source = "empty"
            raw.warnings.append("No se encontró texto nativo en esta página y el OCR está desactivado")
        return raw

    raw = _extract_page_ocr(page, page_number, opts, native_score)
    if not raw.lines:
        raw.warnings.append("La página parece escaneada, pero el OCR no devolvió texto")
    return raw


def _score_native_text(text: str) -> float:
    stripped = text.strip()
    if not stripped:
        return 0.0
    chars = len(stripped)
    words = WORD_RE.findall(stripped)
    letters = sum(1 for ch in stripped if ch.isalpha())
    printable = sum(1 for ch in stripped if ch.isprintable())
    alpha_ratio = letters / max(1, chars)
    printable_ratio = printable / max(1, chars)
    word_score = min(1.0, len(words) / 40.0)
    char_score = min(1.0, chars / 300.0)
    quality_score = max(0.0, min(1.0, 0.55 * alpha_ratio + 0.45 * printable_ratio))
    return round(0.40 * word_score + 0.35 * char_score + 0.25 * quality_score, 4)


def _has_usable_native_text(text: str, score: float, opts: ExtractionOptions) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    words = WORD_RE.findall(stripped)
    letters = sum(1 for ch in stripped if ch.isalpha())
    alpha_ratio = letters / max(1, len(stripped))
    return (
        len(stripped) >= opts.native_min_chars
        and len(words) >= opts.native_min_words
        and alpha_ratio >= 0.18
        and score >= 0.25
    )


def _extract_page_native(page: fitz.Page, page_number: int, native_score: float) -> RawPage:
    page_dict = page.get_text("dict", sort=True)
    rect = page.rect
    lines: list[InternalLine] = []
    image_boxes: list[tuple[float, float, float, float]] = []

    for block_index, block in enumerate(page_dict.get("blocks", [])):
        block_type = block.get("type")
        bbox = _safe_bbox(block.get("bbox"), fallback=(0.0, 0.0, 0.0, 0.0))
        if block_type == 1:
            image_boxes.append(bbox)
            continue
        if block_type != 0:
            continue
        for line_index, line in enumerate(block.get("lines", [])):
            spans = line.get("spans", [])
            text = "".join(span.get("text", "") for span in spans)
            text = _normalize_visible_spaces(text)
            if not text.strip():
                continue
            sizes = [float(span.get("size", 0.0)) for span in spans if span.get("size")]
            font_size = statistics.median(sizes) if sizes else None
            line_bbox = _safe_bbox(line.get("bbox"), fallback=bbox)
            internal = InternalLine(
                page_number=page_number,
                text=text,
                bbox=line_bbox,
                source="native",
                block_id=block_index,
                line_id=line_index,
                confidence=None,
                font_size=font_size,
            )
            lines.append(internal)

    _mark_captions(lines, image_boxes)
    if not lines:
        source: Literal["native", "ocr", "empty", "error"] = "empty"
    else:
        source = "native"
    return RawPage(
        page_number=page_number,
        width=float(rect.width),
        height=float(rect.height),
        source=source,
        native_score=native_score,
        lines=lines,
        image_boxes=image_boxes,
    )


def _extract_page_ocr(page: fitz.Page, page_number: int, opts: ExtractionOptions, native_score: float) -> RawPage:
    rect = page.rect
    scale = opts.ocr_dpi / 72.0
    raw = RawPage(
        page_number=page_number,
        width=float(rect.width),
        height=float(rect.height),
        source="ocr",
        native_score=native_score,
    )

    try:
        matrix = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        data = pytesseract.image_to_data(
            image,
            lang=opts.language,
            config="--oem 1 --psm 3",
            output_type=Output.DICT,
        )
    except pytesseract.TesseractNotFoundError as exc:
        raw.source = "error"
        raw.warnings.append("Tesseract no está instalado o no está en PATH")
        return raw
    except pytesseract.TesseractError as exc:
        raw.source = "error"
        raw.warnings.append(f"Tesseract devolvió un error: {_short_error(str(exc))}")
        return raw
    except Exception as exc:  # pragma: no cover - depends on renderer/system
        raw.source = "error"
        raw.warnings.append(f"No se pudo renderizar/OCR la página: {_short_error(str(exc))}")
        return raw

    grouped: dict[tuple[int, int, int], list[tuple[str, float, float, float, float, float]]] = defaultdict(list)
    confidences: list[float] = []
    n = len(data.get("text", []))
    for i in range(n):
        text = _normalize_visible_spaces(data["text"][i])
        if not text.strip():
            continue
        conf = _to_float(data.get("conf", [None] * n)[i])
        if conf is not None and conf < 0:
            conf = None
        if conf is not None:
            confidences.append(conf)
        left = _to_float(data.get("left", [0] * n)[i]) or 0.0
        top = _to_float(data.get("top", [0] * n)[i]) or 0.0
        width = _to_float(data.get("width", [0] * n)[i]) or 0.0
        height = _to_float(data.get("height", [0] * n)[i]) or 0.0
        key = (
            int(data.get("block_num", [0] * n)[i] or 0),
            int(data.get("par_num", [0] * n)[i] or 0),
            int(data.get("line_num", [0] * n)[i] or 0),
        )
        grouped[key].append((text, left / scale, top / scale, (left + width) / scale, (top + height) / scale, conf if conf is not None else -1.0))

    lines: list[InternalLine] = []
    for raw_line_index, (key, words) in enumerate(sorted(grouped.items(), key=lambda item: item[0])):
        if not words:
            continue
        text = " ".join(word[0] for word in words).strip()
        if not text:
            continue
        x0 = min(word[1] for word in words)
        y0 = min(word[2] for word in words)
        x1 = max(word[3] for word in words)
        y1 = max(word[4] for word in words)
        valid_conf = [word[5] for word in words if word[5] >= 0]
        line_conf = round(sum(valid_conf) / len(valid_conf), 2) if valid_conf else None
        block_id = key[0] * 10000 + key[1]
        line_id = key[2]
        lines.append(
            InternalLine(
                page_number=page_number,
                text=text,
                bbox=(x0, y0, x1, y1),
                source="ocr",
                block_id=block_id,
                line_id=line_id if line_id else raw_line_index,
                confidence=line_conf,
            )
        )

    _mark_captions(lines, [])
    raw.lines = lines
    raw.ocr_mean_confidence = round(sum(confidences) / len(confidences), 2) if confidences else None
    if not raw.lines:
        raw.source = "empty"
    return raw


def _detect_repeated_header_footer_lines(raw_pages: list[RawPage], opts: ExtractionOptions) -> set[str]:
    if not opts.remove_repeated_headers_footers or len(raw_pages) < 2:
        return set()
    counter: Counter[str] = Counter()
    pages_for_norm: dict[str, set[int]] = defaultdict(set)

    for page in raw_pages:
        seen_on_page: set[str] = set()
        for line in page.lines:
            if not _is_in_header_footer_zone(line, page.height, opts.auto_header_footer_zone_pct):
                continue
            norm = _normalize_for_repetition(line.text)
            if not _is_repeat_candidate(norm):
                continue
            if norm not in seen_on_page:
                counter[norm] += 1
                pages_for_norm[norm].add(page.page_number)
                seen_on_page.add(norm)

    selected = len(raw_pages)
    min_occurrences = max(2, math.ceil(opts.repeated_line_min_ratio * selected))
    return {norm for norm, count in counter.items() if count >= min_occurrences}


def _cleanup_and_render_page(raw: RawPage, opts: ExtractionOptions, repeated_norms: set[str]) -> ExtractedPage:
    kept: list[InternalLine] = []
    removed: list[InternalLine] = []
    chars_before = sum(len(line.text) for line in raw.lines)
    words_before = sum(len(WORD_RE.findall(line.text)) for line in raw.lines)

    for line in raw.lines:
        reason = _removal_reason(line, raw, opts, repeated_norms)
        if reason:
            line.removed_reason = reason
            removed.append(line)
        else:
            kept.append(line)

    ordered = _order_lines(kept, raw.width, raw.height, opts)
    text = _render_page_text(ordered, opts)
    chars_after = len(text)
    words_after = len(WORD_RE.findall(text))
    caption_lines = sum(1 for line in kept if line.is_caption)
    report = PageReport(
        page_number=raw.page_number,
        source=raw.source,
        chars_before_cleanup=chars_before,
        chars_after_cleanup=chars_after,
        words_before_cleanup=words_before,
        words_after_cleanup=words_after,
        native_score=raw.native_score,
        ocr_mean_confidence=raw.ocr_mean_confidence,
        removed_lines=len(removed),
        caption_lines=caption_lines,
        warnings=raw.warnings,
    )

    return ExtractedPage(
        page_number=raw.page_number,
        width=raw.width,
        height=raw.height,
        source=raw.source,
        text=text,
        lines=[_to_extracted_line(line) for line in ordered],
        removed_lines=[_to_extracted_line(line) for line in removed],
        report=report,
    )


def _removal_reason(line: InternalLine, raw: RawPage, opts: ExtractionOptions, repeated_norms: set[str]) -> str | None:
    if _outside_user_margins(line, raw.width, raw.height, opts):
        return "margen_configurado"
    if not opts.include_image_captions and line.is_caption:
        return "pie_de_imagen_excluido"
    if opts.strip_page_numbers and _is_page_number(line.text, raw.page_number):
        return "numero_de_pagina"
    if opts.remove_repeated_headers_footers:
        norm = _normalize_for_repetition(line.text)
        if norm in repeated_norms and _is_in_header_footer_zone(line, raw.height, opts.auto_header_footer_zone_pct):
            return "cabecera_o_pie_repetido"
    return None


def _outside_user_margins(line: InternalLine, page_width: float, page_height: float, opts: ExtractionOptions) -> bool:
    left = page_width * opts.left_margin_pct
    right = page_width * (1.0 - opts.right_margin_pct)
    top = page_height * opts.top_margin_pct
    bottom = page_height * (1.0 - opts.bottom_margin_pct)
    return line.cx < left or line.cx > right or line.cy < top or line.cy > bottom


def _order_lines(lines: list[InternalLine], page_width: float, page_height: float, opts: ExtractionOptions) -> list[InternalLine]:
    if not opts.sort_reading_order or len(lines) <= 2:
        return sorted(lines, key=lambda line: (line.y0, line.x0))

    if _looks_two_column(lines, page_width):
        return _order_two_columns_with_full_width(lines, page_width)
    return sorted(lines, key=lambda line: (round(line.y0, 1), round(line.x0, 1)))


def _looks_two_column(lines: list[InternalLine], page_width: float) -> bool:
    body = [line for line in lines if len(line.text.strip()) >= 4]
    if len(body) < 12:
        return False
    mid = page_width / 2.0
    narrow = [line for line in body if (line.x1 - line.x0) < page_width * 0.60]
    if len(narrow) < len(body) * 0.55:
        return False
    left = [line for line in narrow if line.cx < mid * 0.98]
    right = [line for line in narrow if line.cx > mid * 1.02]
    crossing = [line for line in body if line.x0 < mid - 12 and line.x1 > mid + 12 and (line.x1 - line.x0) > page_width * 0.42]
    return len(left) >= 5 and len(right) >= 5 and len(crossing) <= max(4, int(0.30 * len(body)))


def _order_two_columns_with_full_width(lines: list[InternalLine], page_width: float) -> list[InternalLine]:
    mid = page_width / 2.0
    full_width: list[InternalLine] = []
    left_col: list[InternalLine] = []
    right_col: list[InternalLine] = []
    for line in lines:
        width = line.x1 - line.x0
        crosses_mid = line.x0 < mid - 10 and line.x1 > mid + 10
        if width > page_width * 0.58 or crosses_mid:
            full_width.append(line)
        elif line.cx < mid:
            left_col.append(line)
        else:
            right_col.append(line)

    full_width_sorted = sorted(full_width, key=lambda line: (line.y0, line.x0))
    result: list[InternalLine] = []
    lower_bound = -1.0
    for fw_line in full_width_sorted:
        segment_left = [line for line in left_col if lower_bound <= line.y0 < fw_line.y0]
        segment_right = [line for line in right_col if lower_bound <= line.y0 < fw_line.y0]
        result.extend(sorted(segment_left, key=lambda line: (line.y0, line.x0)))
        result.extend(sorted(segment_right, key=lambda line: (line.y0, line.x0)))
        result.append(fw_line)
        lower_bound = fw_line.y1

    segment_left = [line for line in left_col if line.y0 >= lower_bound]
    segment_right = [line for line in right_col if line.y0 >= lower_bound]
    result.extend(sorted(segment_left, key=lambda line: (line.y0, line.x0)))
    result.extend(sorted(segment_right, key=lambda line: (line.y0, line.x0)))

    # Avoid accidentally dropping lines with unusual coordinates.
    seen = {id(line) for line in result}
    result.extend(line for line in sorted(lines, key=lambda line: (line.y0, line.x0)) if id(line) not in seen)
    return result


def _render_page_text(lines: list[InternalLine], opts: ExtractionOptions) -> str:
    if not lines:
        return ""
    if opts.preserve_line_breaks or opts.mode == "faithful":
        return _render_preserved_lines(lines, opts)
    return _render_reflowed_text(lines, opts)


def _render_preserved_lines(lines: list[InternalLine], opts: ExtractionOptions) -> str:
    output: list[str] = []
    previous: InternalLine | None = None
    for line in lines:
        text = _display_line_text(line, opts)
        if previous is not None and opts.mode != "faithful":
            same_block = previous.block_id == line.block_id
            vertical_gap = line.y0 - previous.y1
            large_gap = vertical_gap > max(8.0, previous.height * 1.35)
            backwards_jump = line.y0 + 4 < previous.y0
            if (not same_block and large_gap) or backwards_jump:
                if output and output[-1] != "":
                    output.append("")
        output.append(text)
        previous = line
    return "\n".join(output).strip()


def _render_reflowed_text(lines: list[InternalLine], opts: ExtractionOptions) -> str:
    paragraphs: list[str] = []
    current = ""
    previous: InternalLine | None = None
    for line in lines:
        text = _display_line_text(line, opts).strip()
        if not text:
            continue
        starts_new_paragraph = False
        if previous is not None:
            same_block = previous.block_id == line.block_id
            vertical_gap = line.y0 - previous.y1
            indentation_jump = abs(line.x0 - previous.x0) > 35 and len(previous.text.strip()) > 20
            starts_new_paragraph = (not same_block and vertical_gap > 4) or vertical_gap > max(10.0, previous.height * 1.6) or indentation_jump
        if starts_new_paragraph and current:
            paragraphs.append(current.strip())
            current = text
        else:
            if current.endswith("-") and text and text[0].islower():
                current = current[:-1] + text
            elif current:
                current += " " + text
            else:
                current = text
        previous = line
    if current:
        paragraphs.append(current.strip())
    return "\n\n".join(paragraphs).strip()


def _display_line_text(line: InternalLine, opts: ExtractionOptions) -> str:
    text = line.text.strip()
    if opts.mark_captions and line.is_caption:
        return f"[Pie de imagen] {text}"
    return text


def _compose_full_text(pages: list[ExtractedPage], opts: ExtractionOptions) -> str:
    chunks: list[str] = []
    for page in pages:
        if opts.page_breaks:
            chunks.append(f"--- Página {page.page_number} ---")
        chunks.append(page.text.strip())
    return "\n\n".join(chunk for chunk in chunks if chunk).strip() + "\n"


def _to_extracted_line(line: InternalLine) -> ExtractedLine:
    return ExtractedLine(
        text=line.text,
        bbox=tuple(round(v, 2) for v in line.bbox),
        source=line.source,
        confidence=line.confidence,
        is_caption=line.is_caption,
        removed_reason=line.removed_reason,
    )


def _mark_captions(lines: list[InternalLine], image_boxes: list[tuple[float, float, float, float]]) -> None:
    for line in lines:
        if CAPTION_RE.search(line.text):
            line.is_caption = True
            continue
        for img in image_boxes:
            if _line_near_image(line.bbox, img):
                line.is_caption = True
                break


def _line_near_image(line: tuple[float, float, float, float], img: tuple[float, float, float, float]) -> bool:
    lx0, ly0, lx1, ly1 = line
    ix0, iy0, ix1, iy1 = img
    horizontal_overlap = max(0.0, min(lx1, ix1) - max(lx0, ix0))
    min_width = max(1.0, min(lx1 - lx0, ix1 - ix0))
    overlap_ratio = horizontal_overlap / min_width
    below_distance = ly0 - iy1
    above_distance = iy0 - ly1
    return overlap_ratio >= 0.30 and (0.0 <= below_distance <= 75.0 or 0.0 <= above_distance <= 35.0)


def _is_in_header_footer_zone(line: InternalLine, page_height: float, zone_pct: float) -> bool:
    return line.cy <= page_height * zone_pct or line.cy >= page_height * (1.0 - zone_pct)


def _normalize_for_repetition(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().strip()
    text = re.sub(r"\d+", "#", text)
    text = re.sub(r"\b[ivxlcdm]{1,8}\b", "#", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w#áéíóúüñ ]+", " ", text, flags=re.IGNORECASE)
    return WHITESPACE_RE.sub(" ", text).strip()


def _is_repeat_candidate(norm: str) -> bool:
    if len(norm) < 4:
        return False
    alpha = sum(1 for ch in norm if ch.isalpha())
    if alpha < 3 and "#" not in norm:
        return False
    if len(norm.split()) > 16:
        return False
    return True


def _is_page_number(text: str, page_number: int) -> bool:
    stripped = text.strip()
    if PAGE_NUMBER_RE.match(stripped):
        return True
    # Some books use the logical number with decoration: "5 | Título"
    if re.fullmatch(rf"\s*{page_number}\s*[|/\\]\s*", stripped):
        return True
    return False


def _safe_bbox(value: Iterable[float] | None, fallback: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    try:
        if value is None:
            return fallback
        values = tuple(float(v) for v in value)
        if len(values) != 4:
            return fallback
        return values  # type: ignore[return-value]
    except Exception:
        return fallback


def _normalize_visible_spaces(text: str) -> str:
    return text.replace("\u00a0", " ").replace("\ufeff", "").rstrip()


def _to_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _short_error(value: str, max_len: int = 250) -> str:
    cleaned = WHITESPACE_RE.sub(" ", value).strip()
    return cleaned if len(cleaned) <= max_len else cleaned[: max_len - 1] + "…"
