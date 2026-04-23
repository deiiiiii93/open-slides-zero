"""Stage B — parse uploaded materials into normalized text + metadata.

Supported inputs:
  - inline / local text: txt, md, markdown
  - office docs: docx, pptx, xlsx
  - pdf: selectable text only, with image-only page warnings
  - raster images: ZenMux OCR model
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from ...llm import zenmux
from ...llm.models import get_model

log = logging.getLogger(__name__)

TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


@dataclass
class ParsedMaterial:
    text: str
    note: str | None = None


def _append_note(existing: str | None, extra: str | None) -> str | None:
    if existing and extra:
        return f"{existing}\n{extra}"
    return extra or existing


def _escape_table_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", "<br>")


def _markdown_table(rows: list[list[str]]) -> str:
    normalized = [
        [cell.strip() for cell in row]
        for row in rows
        if any(cell.strip() for cell in row)
    ]
    if not normalized:
        return ""
    width = max(len(row) for row in normalized)
    padded = [row + [""] * (width - len(row)) for row in normalized]
    header = padded[0]
    separator = ["---"] * width
    lines = [
        "| " + " | ".join(_escape_table_cell(cell) for cell in header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in padded[1:]:
        lines.append("| " + " | ".join(_escape_table_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


def _finalize_text(text: str, note: str | None = None) -> ParsedMaterial:
    normalized = text.strip()
    if normalized:
        return ParsedMaterial(text=normalized, note=note)
    return ParsedMaterial(text="", note=note or "No extractable content found.")


def _parse_image_ocr(uri: str) -> ParsedMaterial:
    prompt = (
        "Extract slide-source material from this image using OCR.\n"
        "1. Transcribe visible text faithfully, preserving the original language.\n"
        "2. Reconstruct tables as Markdown when possible.\n"
        "3. Include chart titles, axes labels, legends, units, and notable values.\n"
        "4. If the image is mostly non-text, append a short 'Visual summary' section "
        "covering the key visual evidence.\n"
        "Return plain text only."
    )
    text = zenmux.chat(
        get_model("ingest.ocr"),
        [{"role": "user", "content": prompt}],
        images=[uri],
        temperature=0.1,
    )
    return _finalize_text(text)


def _read_text_file(uri: str) -> ParsedMaterial:
    if uri.startswith("text:"):
        return _finalize_text(uri[len("text:"):], note=None)
    path = Path(uri)
    if not path.exists():
        raise FileNotFoundError(f"Material file not found: {uri}")
    return _finalize_text(path.read_text(encoding="utf-8", errors="replace"))


def _parse_pdf(uri: str) -> ParsedMaterial:
    from pypdf import PdfReader

    reader = PdfReader(uri)
    blocks: list[str] = []
    image_only_pages: list[int] = []
    for idx, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            blocks.append(f"## Page {idx}\n{text}")
        else:
            image_only_pages.append(idx)
    note = None
    if image_only_pages:
        pages = ", ".join(str(page) for page in image_only_pages)
        note = f"Pages {pages} had no extractable PDF text and were treated as image-only."
    return _finalize_text("\n\n".join(blocks), note=note)


def _parse_docx(uri: str) -> ParsedMaterial:
    from docx import Document

    document = Document(uri)
    blocks: list[str] = []

    paragraphs = [p.text.strip() for p in document.paragraphs if p.text and p.text.strip()]
    if paragraphs:
        blocks.append("\n".join(paragraphs))

    for idx, table in enumerate(document.tables, start=1):
        rows = [
            [cell.text.strip() for cell in row.cells]
            for row in table.rows
        ]
        table_md = _markdown_table(rows)
        if table_md:
            blocks.append(f"## Table {idx}\n{table_md}")

    return _finalize_text("\n\n".join(blocks))


def _extract_notes_text(slide: Any) -> str:
    texts: list[str] = []
    try:
        notes_slide = slide.notes_slide
    except Exception:
        return ""
    for shape in getattr(notes_slide, "shapes", []):
        text = getattr(shape, "text", "")
        if text and text.strip():
            texts.append(text.strip())
    return "\n".join(texts)


def _parse_pptx(uri: str) -> ParsedMaterial:
    from pptx import Presentation

    presentation = Presentation(uri)
    slides: list[str] = []
    for idx, slide in enumerate(presentation.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                text = getattr(shape, "text", "").strip()
                if text:
                    parts.append(text)
            if getattr(shape, "has_table", False):
                rows = [
                    [cell.text.strip() for cell in row.cells]
                    for row in shape.table.rows
                ]
                table_md = _markdown_table(rows)
                if table_md:
                    parts.append(f"Table\n{table_md}")
        notes_text = _extract_notes_text(slide)
        if notes_text:
            parts.append(f"Speaker notes\n{notes_text}")
        if parts:
            slides.append(f"## Slide {idx}\n" + "\n\n".join(parts))

    return _finalize_text("\n\n".join(slides))


def _cell_to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_xlsx(uri: str) -> ParsedMaterial:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    workbook = load_workbook(uri, data_only=True, read_only=True)
    blocks: list[str] = []

    for sheet in workbook.worksheets:
        all_rows = list(sheet.iter_rows(values_only=True))
        min_row: int | None = None
        max_row = 0
        min_col: int | None = None
        max_col = 0
        for row_idx, row in enumerate(all_rows, start=1):
            for col_idx, value in enumerate(row, start=1):
                if _cell_to_text(value):
                    min_row = row_idx if min_row is None else min(min_row, row_idx)
                    max_row = max(max_row, row_idx)
                    min_col = col_idx if min_col is None else min(min_col, col_idx)
                    max_col = max(max_col, col_idx)
        if min_row is None or min_col is None:
            continue

        extracted_rows: list[list[str]] = []
        for row_idx in range(min_row, max_row + 1):
            source_row = all_rows[row_idx - 1] if row_idx - 1 < len(all_rows) else ()
            extracted_rows.append(
                [
                    _cell_to_text(source_row[col_idx - 1] if col_idx - 1 < len(source_row) else None)
                    for col_idx in range(min_col, max_col + 1)
                ]
            )
        table_md = _markdown_table(extracted_rows)
        if table_md:
            coord = f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{max_row}"
            blocks.append(f"## Sheet: {sheet.title}\nRange: {coord}\n{table_md}")

    return _finalize_text("\n\n".join(blocks))


def _parse_file_material(uri: str) -> ParsedMaterial:
    if uri.startswith("text:"):
        return _read_text_file(uri)
    path = Path(uri)
    if not path.exists():
        raise FileNotFoundError(f"Material file not found: {uri}")
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return _read_text_file(uri)
    if suffix in IMAGE_SUFFIXES:
        return _parse_image_ocr(uri)
    if suffix == ".pdf":
        return _parse_pdf(uri)
    if suffix == ".docx":
        return _parse_docx(uri)
    if suffix == ".pptx":
        return _parse_pptx(uri)
    if suffix == ".xlsx":
        return _parse_xlsx(uri)
    raise ValueError(f"Unsupported material type: {suffix or 'unknown'}")


def ingest_node(state: dict[str, Any]) -> dict[str, Any]:
    materials = state.get("materials", [])
    out: list[dict[str, Any]] = []
    for material in materials:
        if material.get("parsed"):
            out.append(material)
            continue
        try:
            parsed = _parse_file_material(material["uri"])
            out.append(
                {
                    **material,
                    "parsed": parsed.text,
                    "note": _append_note(material.get("note"), parsed.note),
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Failed to parse material %s", material.get("uri"))
            out.append(
                {
                    **material,
                    "parsed": "",
                    "note": _append_note(material.get("note"), f"Failed to parse: {exc}"),
                }
            )

    if not any((material.get("parsed") or "").strip() for material in out):
        raise ValueError("No usable content could be extracted from the provided materials.")

    return {
        "materials": out,
        "current_stage": "outline",
    }
