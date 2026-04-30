"""Deck lifecycle endpoints.

GET  /decks          — list all decks from the checkpointer DB.
POST /decks          — create a new thread and kick off the pipeline (up to structure gate).
GET  /decks/{id}     — return the current snapshot (state values + next + interrupt payload).
GET  /decks/{id}/catalog — return structure/scenario/layout catalogs (for HITL UIs).
POST /decks/{id}/materials — upload a file to attach as material.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..artifacts import store
from ..catalog.layouts import PATTERNS
from ..catalog.scenarios import SCENARIO_DEFINITIONS
from ..catalog.structures import STRUCTURE_DEFINITIONS
from ..catalog.visual_presets import list_visual_style_presets, visual_style_preset_state
from ..graph.graph import DB_PATH
from ..llm.models import normalize_lane_model_overrides
from .common import config_for, current_state, delete_thread, graph, mirror_to_disk

router = APIRouter()
SUPPORTED_UPLOAD_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".pdf",
    ".pptx",
    ".jpg",
    ".jpeg",
    ".png",
    ".docx",
    ".xlsx",
}
IMAGE_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MAX_UPLOAD_BYTES = int(os.getenv("OSZ_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
MAX_REQUEST_BYTES = int(os.getenv("OSZ_MAX_REQUEST_BYTES", str(100 * 1024 * 1024)))
_UPLOAD_CHUNK = 1 << 20  # 1 MiB read chunk


class Material(BaseModel):
    kind: str = Field(description="text | file | image")
    uri: str
    name: str | None = None
    note: str | None = None


class CreateDeckBody(BaseModel):
    deck_name: str | None = None
    expected_pages: int = 10
    aspect_ratio: str = "16:9"
    density_preference: str = "balanced"
    language: str = "en"
    visual_style_preference: str | None = None
    visual_style_preset_id: str | None = None
    style_reference_image_uri: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    model_overrides: dict[str, str] | None = None
    materials: list[Material] = Field(default_factory=list)


def _serialize_material(material: Material | dict[str, Any]) -> dict[str, Any]:
    return material.model_dump() if isinstance(material, Material) else dict(material)


def _material_name(material: Material | dict[str, Any]) -> str | None:
    data = _serialize_material(material)
    if data.get("name"):
        return str(data["name"])
    uri = str(data.get("uri") or "")
    if uri.startswith("text:"):
        return None
    name = Path(uri).name
    return name or None


def _coerce_text_material(text: str) -> Material:
    return Material(kind="text", uri=f"text:{text}")


def _coerce_image_url_material(url: str) -> Material:
    parsed = urlparse(url)
    name = Path(parsed.path).name or parsed.netloc or "image-url"
    return Material(kind="image", uri=url, name=name, note="User-provided image URL.")


def _normalize_image_urls(image_urls: Sequence[str] | None) -> list[Material]:
    materials: list[Material] = []
    for raw in image_urls or []:
        url = str(raw).strip()
        if not url:
            continue
        if not url.startswith(("http://", "https://", "data:image/")):
            raise ValueError(f"Image URL must be http(s) or data:image URI: {url}")
        materials.append(_coerce_image_url_material(url))
    return materials


def _derive_deck_name_from_materials(
    deck_name: str | None,
    materials: Sequence[Material | dict[str, Any]],
) -> str:
    if deck_name:
        return deck_name
    for material in materials:
        data = _serialize_material(material)
        if data.get("kind") == "text" and str(data.get("uri") or "").startswith("text:"):
            text = str(data["uri"]).removeprefix("text:")
            first = text.strip().split("\n")[0]
            name = first[:60] + ("…" if len(first) > 60 else "")
            if name:
                return name
    for material in materials:
        if name := _material_name(material):
            return Path(name).stem or "Untitled deck"
    return "Untitled deck"


def _build_initial_state(
    *,
    thread_id: str,
    deck_name: str | None,
    materials: Sequence[Material | dict[str, Any]],
    expected_pages: int,
    aspect_ratio: str,
    density_preference: str,
    language: str,
    visual_style_preference: str | None,
    visual_style_preset_id: str | None,
    style_reference_image_uri: str | None,
    model_overrides: dict[str, str] | None = None,
    image_urls: Sequence[str] | None = None,
) -> dict[str, Any]:
    all_materials = [*materials, *_normalize_image_urls(image_urls)]
    if not all_materials:
        raise ValueError("Provide at least one file or paste text.")
    normalized_model_overrides = normalize_lane_model_overrides(model_overrides)
    initial = {
        "thread_id": thread_id,
        "deck_name": _derive_deck_name_from_materials(deck_name, all_materials),
        "materials": [_serialize_material(material) for material in all_materials],
        "expected_pages": expected_pages,
        "aspect_ratio": aspect_ratio,
        "density_preference": density_preference,
        "language": language,
        "visual_style_preference": visual_style_preference,
        "style_reference_image_uri": style_reference_image_uri,
        "image_urls": list(image_urls or []),
        "lane_model_overrides": normalized_model_overrides or None,
        "current_stage": "ingest",
    }
    initial.update(visual_style_preset_state(visual_style_preset_id))
    return initial


def _sanitize_upload_filename(filename: str | None) -> str:
    candidate = Path(filename or f"upload-{uuid.uuid4().hex[:8]}").name
    if not candidate or candidate in {".", ".."}:
        return f"upload-{uuid.uuid4().hex[:8]}"
    return candidate


def _validate_upload_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_UPLOAD_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_UPLOAD_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type for '{filename}'. Supported types: {supported}",
        )
    return ext


def _reserve_material_path(thread_id: str, desired_name: str) -> Path:
    """Pick a unique path under the thread's materials dir (dup.txt → dup-2.txt)."""
    dest_dir = store.thread_dir(thread_id) / "materials"
    candidate = dest_dir / desired_name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    counter = 2
    while True:
        candidate = dest_dir / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


async def _store_upload(thread_id: str, file: UploadFile) -> tuple[Material, int]:
    filename = _sanitize_upload_filename(file.filename)
    ext = _validate_upload_extension(filename)
    dest_dir = store.thread_dir(thread_id) / "materials"
    tmp_path = dest_dir / f".tmp-{uuid.uuid4().hex[:12]}"
    total = 0
    final_path: Path | None = None
    try:
        with tmp_path.open("wb") as fh:
            while chunk := await file.read(_UPLOAD_CHUNK):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"'{filename}' exceeds the "
                            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB per-file upload limit."
                        ),
                    )
                fh.write(chunk)
        final_path = _reserve_material_path(thread_id, filename)
        tmp_path.replace(final_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    kind = "image" if ext in IMAGE_UPLOAD_EXTENSIONS else "file"
    material = Material(kind=kind, uri=str(final_path), name=filename)
    return material, total


async def normalize_uploaded_materials(
    thread_id: str,
    files: list[UploadFile],
) -> list[Material]:
    materials: list[Material] = []
    total_bytes = 0
    for file in files:
        material, size = await _store_upload(thread_id, file)
        total_bytes += size
        if total_bytes > MAX_REQUEST_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Upload total exceeds the "
                    f"{MAX_REQUEST_BYTES // (1024 * 1024)} MB per-request limit."
                ),
            )
        materials.append(material)
    return materials


@router.get("/decks")
def list_decks() -> dict[str, Any]:
    """Return all thread IDs from the checkpointer DB with basic metadata."""
    db = DB_PATH
    if not db.exists():
        return {"decks": []}
    conn = sqlite3.connect(str(db))
    try:
        cursor = conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id"
        )
        thread_ids = [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()

    decks = []
    for tid in thread_ids:
        try:
            state = current_state(tid)
            values = state.get("values") or {}
            decks.append(
                {
                    "thread_id": tid,
                    "deck_name": values.get("deck_name") or tid,
                    "stage": values.get("current_stage") or "unknown",
                    "created_at": state.get("created_at"),
                }
            )
        except Exception:
            # Skip threads whose state cannot be read
            decks.append(
                {
                    "thread_id": tid,
                    "deck_name": tid,
                    "stage": "unknown",
                    "created_at": None,
                }
            )
    # Most recently active first (sort by created_at descending, None last)
    decks.sort(key=lambda d: (d["created_at"] is None, d["created_at"] or ""), reverse=True)
    return {"decks": decks}


@router.post("/decks")
def create_deck(body: CreateDeckBody) -> dict[str, Any]:
    thread_id = uuid.uuid4().hex[:12]
    cfg = config_for(thread_id)
    try:
        initial = _build_initial_state(
            thread_id=thread_id,
            deck_name=body.deck_name,
            materials=body.materials,
            expected_pages=body.expected_pages,
            aspect_ratio=body.aspect_ratio,
            density_preference=body.density_preference,
            language=body.language,
            visual_style_preference=body.visual_style_preference,
            visual_style_preset_id=body.visual_style_preset_id,
            style_reference_image_uri=body.style_reference_image_uri,
            model_overrides=body.model_overrides,
            image_urls=body.image_urls,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Invoke runs until the first interrupt (structure gate).
    try:
        graph().invoke(initial, cfg)  # type: ignore[arg-type]
    except ValueError as exc:
        delete_thread(thread_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    mirror_to_disk(thread_id)
    return current_state(thread_id)


@router.get("/decks/{thread_id}")
def get_deck(thread_id: str) -> dict[str, Any]:
    state = current_state(thread_id)
    if not state["values"]:
        raise HTTPException(status_code=404, detail="Unknown deck")
    return state


@router.get("/decks/{thread_id}/catalog")
def get_catalog(thread_id: str) -> dict[str, Any]:
    # thread_id is accepted for symmetry but catalog is global.
    return {
        "scenarios": SCENARIO_DEFINITIONS,
        "structures": [
            {"id": sid, **{k: v for k, v in s.items() if k != "id"}}
            for sid, s in STRUCTURE_DEFINITIONS.items()
            if not s.get("legacy")
        ],
        "patterns": {pid: p for pid, p in PATTERNS.items()},
        "visual_style_presets": list_visual_style_presets(),
    }


@router.post("/decks/{thread_id}/materials")
async def upload_material(thread_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    material, size = await _store_upload(thread_id, file)
    return {**material.model_dump(), "bytes": size}


@router.get("/decks/{thread_id}/slides/{slide_idx}")
def get_slide_html(thread_id: str, slide_idx: int) -> dict[str, Any]:
    snap = graph().get_state(config_for(thread_id))  # type: ignore[arg-type]
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown deck")
    slides = snap.values.get("html_slides") or {}
    html = slides.get(slide_idx) or slides.get(str(slide_idx))
    if not html:
        raise HTTPException(status_code=404, detail="Slide not rendered yet")
    return {"slide_idx": slide_idx, "html": html}
