"""Deck lifecycle endpoints.

POST /decks          — create a new thread and kick off the pipeline (up to structure gate).
GET  /decks/{id}     — return the current snapshot (state values + next + interrupt payload).
GET  /decks/{id}/catalog — return structure/scenario/layout catalogs (for HITL UIs).
POST /decks/{id}/materials — upload a file to attach as material.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..artifacts import store
from ..catalog.layouts import PATTERNS
from ..catalog.scenarios import SCENARIO_DEFINITIONS
from ..catalog.structures import STRUCTURE_DEFINITIONS
from .common import config_for, current_state, graph, mirror_to_disk

router = APIRouter()


class Material(BaseModel):
    kind: str = Field(description="text | file | image")
    uri: str
    note: str | None = None


class CreateDeckBody(BaseModel):
    expected_pages: int = 10
    aspect_ratio: str = "16:9"
    density_preference: str = "balanced"
    language: str = "en"
    visual_style_preference: str | None = None
    style_reference_image_uri: str | None = None
    materials: list[Material] = Field(default_factory=list)


@router.post("/decks")
def create_deck(body: CreateDeckBody) -> dict[str, Any]:
    thread_id = uuid.uuid4().hex[:12]
    cfg = config_for(thread_id)
    initial: dict[str, Any] = {
        "thread_id": thread_id,
        "materials": [m.model_dump() for m in body.materials],
        "expected_pages": body.expected_pages,
        "aspect_ratio": body.aspect_ratio,
        "density_preference": body.density_preference,
        "language": body.language,
        "visual_style_preference": body.visual_style_preference,
        "style_reference_image_uri": body.style_reference_image_uri,
        "current_stage": "ingest",
    }
    # Invoke runs until the first interrupt (structure gate).
    graph().invoke(initial, cfg)  # type: ignore[arg-type]
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
    }


@router.post("/decks/{thread_id}/materials")
async def upload_material(thread_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    contents = await file.read()
    filename = file.filename or f"upload-{uuid.uuid4().hex[:8]}"
    path = store.save_material(thread_id, filename, contents)
    ext = Path(filename).suffix.lower()
    kind = "image" if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif") else "file"
    return {"kind": kind, "uri": str(path), "name": filename, "bytes": len(contents)}


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
