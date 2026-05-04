"""Server-Sent Events streaming variants of deck create / resume.

Uses LangGraph's `graph.stream(..., stream_mode=["custom", "values", "updates"])`:
  - "custom"  — events from `push_token()` / `push_event()` inside nodes
  - "updates" — per-node state deltas as the graph progresses
  - "values"  — full state snapshots (coarser; we use it for the final emission)

Event formats on the wire (SSE data field is JSON):
  {"type": "token", "tag": "outline", "text": "..."}
  {"type": "event", "node": "outline", "state": "started"}
  {"type": "update", "node": "outline", "patch": {...}}
  {"type": "interrupt", "payload": {...}}
  {"type": "done", "state": {...}}
  {"type": "error", "message": "..."}
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Iterable, Iterator

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from .common import (
    config_for,
    current_state,
    delete_thread,
    graph,
    mirror_to_disk,
    resume_synthetic_interrupt,
)
from .decks import (
    CreateDeckBody,
    _build_initial_state,
    _coerce_text_material,
    normalize_uploaded_materials,
)
from .playground_store import is_cutoff_thread

router = APIRouter()
log = logging.getLogger(__name__)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # disable nginx buffering if ever proxied
    "Content-Type": "text/event-stream; charset=utf-8",
}


def _sse(event: dict[str, Any]) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def _stream_graph(
    input_payload: Any,
    cfg: dict[str, Any],
    thread_id: str,
    *,
    cleanup_on_error: bool = False,
) -> Iterator[str]:
    """Run graph.stream() and serialize chunks as SSE. Mirrors final state to disk.

    When called from a create path (where a failure leaves a thread nobody can
    resume), pass cleanup_on_error=True and we'll drop the thread's checkpoints
    + artifacts after emitting the error frame. Resume paths leave it false
    so users can retry a mid-pipeline failure.
    """
    g = graph()
    try:
        for mode, chunk in g.stream(
            input_payload,
            cfg,  # type: ignore[arg-type]
            stream_mode=["custom", "updates"],
        ):
            if mode == "custom":
                ch = chunk.get("channel") if isinstance(chunk, dict) else None
                if ch == "tokens":
                    yield _sse({
                        "type": "token",
                        "tag": chunk.get("tag"),
                        "text": chunk.get("text", ""),
                    })
                elif ch == "event":
                    ev = {k: v for k, v in chunk.items() if k != "channel"}
                    ev["type"] = "event"
                    yield _sse(ev)
            elif mode == "updates":
                # chunk is {node_name: state_patch}
                if isinstance(chunk, dict):
                    for node, patch in chunk.items():
                        safe_patch = _safe_patch(patch)
                        yield _sse({"type": "update", "node": node, "patch": safe_patch})
        # Finalize: emit interrupt (if any) + final state.
        snap = g.get_state(cfg)  # type: ignore[arg-type]
        if snap and snap.interrupts:
            payload = snap.interrupts[0]
            value = payload.value if hasattr(payload, "value") else payload
            yield _sse({"type": "interrupt", "payload": value})
        yield _sse({"type": "done", "state": current_state(thread_id)})
        mirror_to_disk(thread_id)
    except Exception as e:
        log.exception("stream failed for %s", thread_id)
        yield _sse({"type": "error", "message": str(e)})
        if cleanup_on_error:
            delete_thread(thread_id)
        else:
            try:
                yield _sse({"type": "done", "state": current_state(thread_id)})
                mirror_to_disk(thread_id)
            except Exception:
                log.exception("failed to emit recovery state for %s", thread_id)


def _safe_patch(patch: Any) -> dict[str, Any]:
    """Strip large / non-JSON-serializable fields from update patches.

    Full HTML strings are sent separately via state-change events so we don't
    double-emit them here; large text blobs are truncated for the over-the-wire
    deltas (the frontend refetches full state on `done`).
    """
    if not isinstance(patch, dict):
        return {"_repr": repr(patch)[:200]}
    out: dict[str, Any] = {}
    for k, v in patch.items():
        if k == "html_slides" and isinstance(v, dict):
            out[k] = {"slide_count": len(v)}
            continue
        if isinstance(v, str) and len(v) > 2000:
            out[k] = {"_truncated": True, "chars": len(v), "head": v[:200]}
            continue
        try:
            json.dumps(v, ensure_ascii=False)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = {"_repr": repr(v)[:200]}
    return out


def _parse_model_overrides(raw: str | None) -> dict[str, str] | None:
    if raw is None or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="model_overrides must be valid JSON.") from exc
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="model_overrides must be a JSON object.")
    return {str(k): str(v) for k, v in value.items()}


def _parse_thinking_effort_overrides(raw: str | None) -> dict[str, str] | None:
    if raw is None or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="thinking_effort_overrides must be valid JSON.",
        ) from exc
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=400,
            detail="thinking_effort_overrides must be a JSON object.",
        )
    return {str(k): str(v) for k, v in value.items()}


def _parse_image_urls(raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return [line.strip() for line in raw.splitlines() if line.strip()]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="image_urls must be a JSON list or newline list.")
    return [str(item).strip() for item in value if str(item).strip()]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/decks/stream")
def stream_create_deck(body: CreateDeckBody) -> StreamingResponse:
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
            thinking_effort_overrides=body.thinking_effort_overrides,
            image_urls=body.image_urls,
            agent_mode=body.agent_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def gen() -> Iterable[str]:
        yield _sse({"type": "thread", "thread_id": thread_id})
        yield from _stream_graph(initial, cfg, thread_id, cleanup_on_error=True)

    return StreamingResponse(gen(), headers=SSE_HEADERS)


@router.post("/decks/upload/stream")
async def stream_create_deck_uploads(
    deck_name: str | None = Form(default=None),
    text: str = Form(default=""),
    expected_pages: int = Form(default=10),
    aspect_ratio: str = Form(default="16:9"),
    density_preference: str = Form(default="balanced"),
    agent_mode: str = Form(default="default"),
    language: str = Form(default="en"),
    visual_style_preference: str | None = Form(default=None),
    visual_style_preset_id: str | None = Form(default=None),
    image_urls: str | None = Form(default=None),
    model_overrides: str | None = Form(default=None),
    thinking_effort_overrides: str | None = Form(default=None),
    files: list[UploadFile] = File(...),
) -> StreamingResponse:
    thread_id = uuid.uuid4().hex[:12]
    cfg = config_for(thread_id)
    try:
        materials: list[Any] = []
        if text.strip():
            materials.append(_coerce_text_material(text))
        materials.extend(await normalize_uploaded_materials(thread_id, files))
        initial = _build_initial_state(
            thread_id=thread_id,
            deck_name=deck_name,
            materials=materials,
            expected_pages=expected_pages,
            aspect_ratio=aspect_ratio,
            density_preference=density_preference,
            language=language,
            visual_style_preference=visual_style_preference,
            visual_style_preset_id=visual_style_preset_id,
            style_reference_image_uri=None,
            model_overrides=_parse_model_overrides(model_overrides),
            thinking_effort_overrides=_parse_thinking_effort_overrides(
                thinking_effort_overrides
            ),
            image_urls=_parse_image_urls(image_urls),
            agent_mode=agent_mode,
        )
    except HTTPException:
        delete_thread(thread_id)
        raise
    except ValueError as exc:
        delete_thread(thread_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def gen() -> Iterable[str]:
        yield _sse({"type": "thread", "thread_id": thread_id})
        yield from _stream_graph(initial, cfg, thread_id, cleanup_on_error=True)

    return StreamingResponse(gen(), headers=SSE_HEADERS)


class ResumeBody(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/decks/{thread_id}/resume/stream")
def stream_resume(thread_id: str, body: ResumeBody) -> StreamingResponse:
    # Resume does not re-accept thinking_effort_overrides: effort is frozen at
    # lane/deck creation, persisted in lane_thinking_effort_overrides, and read
    # from state by each subagent. To change effort, create a new lane.
    if is_cutoff_thread(thread_id):
        raise HTTPException(status_code=409, detail="Playground lane is cut off.")
    snap = graph().get_state(config_for(thread_id))  # type: ignore[arg-type]
    if not snap:
        raise HTTPException(status_code=404, detail="Unknown deck")
    cfg = config_for(thread_id)

    if snap.interrupts:
        input_payload: Any = Command(resume=body.payload)
    else:
        recovered_cfg = resume_synthetic_interrupt(thread_id, body.payload)
        if recovered_cfg is not None:
            cfg = recovered_cfg
            input_payload = None
        else:
            # No interrupt: treat as "continue from wherever the graph is paused"
            # (e.g. a failed node that's been retried / patched).
            input_payload = None

    def gen() -> Iterable[str]:
        yield _sse({"type": "thread", "thread_id": thread_id})
        yield from _stream_graph(input_payload, cfg, thread_id)

    return StreamingResponse(gen(), headers=SSE_HEADERS)
