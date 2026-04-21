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

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from .common import config_for, current_state, graph, mirror_to_disk
from .decks import CreateDeckBody

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
) -> Iterator[str]:
    """Run graph.stream() and serialize chunks as SSE. Mirrors final state to disk."""
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/decks/stream")
def stream_create_deck(body: CreateDeckBody) -> StreamingResponse:
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

    def gen() -> Iterable[str]:
        yield _sse({"type": "thread", "thread_id": thread_id})
        yield from _stream_graph(initial, cfg, thread_id)

    return StreamingResponse(gen(), headers=SSE_HEADERS)


class ResumeBody(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/decks/{thread_id}/resume/stream")
def stream_resume(thread_id: str, body: ResumeBody) -> StreamingResponse:
    snap = graph().get_state(config_for(thread_id))  # type: ignore[arg-type]
    if not snap:
        raise HTTPException(status_code=404, detail="Unknown deck")
    cfg = config_for(thread_id)

    if snap.interrupts:
        input_payload: Any = Command(resume=body.payload)
    else:
        # No interrupt: treat as "continue from wherever the graph is paused"
        # (e.g. a failed node that's been retried / patched).
        input_payload = None

    def gen() -> Iterable[str]:
        yield _sse({"type": "thread", "thread_id": thread_id})
        yield from _stream_graph(input_payload, cfg, thread_id)

    return StreamingResponse(gen(), headers=SSE_HEADERS)
