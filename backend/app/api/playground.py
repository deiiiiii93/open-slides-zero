"""Creator playground endpoints.

The base deck stops after outline generation. Each lane is a separate LangGraph
thread cloned from that outline checkpoint and then resumed through style,
layout, and HTML with its own creator prompt.
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import playground_store
from .common import config_for, current_state, graph
from .history import _clone_checkpoint_lineage
from .streaming import SSE_HEADERS, _sse, _stream_graph

router = APIRouter()


class CreateLaneBody(BaseModel):
    creator_prompt: str = ""


def _lane_response(row: dict[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] | None
    try:
        state = current_state(row["lane_thread_id"])
    except Exception:
        state = None
    return {
        "lane_id": row["lane_id"],
        "lane_thread_id": row["lane_thread_id"],
        "creator_prompt": row["creator_prompt"],
        "cutoff": bool(row["cutoff"]),
        "created_at": row["created_at"],
        "state": state,
    }


def _find_outline_interrupt_checkpoint(thread_id: str) -> dict[str, Any] | None:
    g = graph()
    cfg = config_for(thread_id)
    for snap in g.get_state_history(cfg):  # type: ignore[arg-type]
        values = snap.values or {}
        if "await_outline" in (snap.next or ()) and values.get("outline_slides"):
            return snap.config
    return None


def _require_playground_base(thread_id: str) -> dict[str, Any]:
    snap = graph().get_state(config_for(thread_id))  # type: ignore[arg-type]
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown deck")
    if snap.values.get("current_stage") != "playground":
        raise HTTPException(
            status_code=409,
            detail="Enable playground mode at the outline gate before creating lanes.",
        )
    return snap.values


def _prepare_lane(thread_id: str, body: CreateLaneBody) -> tuple[dict[str, Any], dict[str, Any]]:
    base_values = _require_playground_base(thread_id)
    target_cfg = _find_outline_interrupt_checkpoint(thread_id)
    if target_cfg is None:
        raise HTTPException(status_code=409, detail="No outline checkpoint found for playground.")

    lane_thread_id = uuid.uuid4().hex[:12]
    creator_prompt = body.creator_prompt.strip()
    try:
        lane = playground_store.create_lane_record(
            thread_id,
            lane_thread_id,
            creator_prompt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        new_target_cfg = _clone_checkpoint_lineage(thread_id, target_cfg, lane_thread_id)
        lane_cfg = graph().update_state(  # type: ignore[arg-type]
            new_target_cfg,
            {
                "thread_id": lane_thread_id,
                "deck_name": f"{base_values.get('deck_name') or thread_id} · {lane['lane_id']}",
                "parent_thread_id": thread_id,
                "lane_id": lane["lane_id"],
                "creator_prompt": creator_prompt,
                "current_stage": "style",
            },
            as_node="await_outline",
        )
    except Exception:
        playground_store.delete_lane_record(thread_id, lane["lane_id"])
        raise

    return lane, lane_cfg


@router.get("/decks/{thread_id}/playground/lanes")
def list_playground_lanes(thread_id: str) -> dict[str, Any]:
    _require_playground_base(thread_id)
    rows = playground_store.list_lanes(thread_id)
    return {
        "max_lanes": playground_store.MAX_LANES_PER_DECK,
        "lanes": [_lane_response(row) for row in rows],
    }


@router.post("/decks/{thread_id}/playground/lanes/stream")
def create_playground_lane_stream(
    thread_id: str,
    body: CreateLaneBody,
) -> StreamingResponse:
    lane, lane_cfg = _prepare_lane(thread_id, body)

    def gen() -> Iterable[str]:
        yield _sse({
            "type": "thread",
            "thread_id": lane["lane_thread_id"],
            "parent_thread_id": thread_id,
            "lane_id": lane["lane_id"],
        })
        yield _sse({"type": "lane", "lane": _lane_response(lane)})
        yield from _stream_graph(None, lane_cfg, lane["lane_thread_id"])

    return StreamingResponse(gen(), headers=SSE_HEADERS)


@router.post("/decks/{thread_id}/playground/lanes/{lane_id}/cutoff")
def cutoff_playground_lane(thread_id: str, lane_id: str) -> dict[str, Any]:
    _require_playground_base(thread_id)
    row = playground_store.mark_lane_cutoff(thread_id, lane_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown playground lane")
    return {"ok": True, "lane": _lane_response(row)}


@router.get("/masterpieces")
def list_masterpieces() -> dict[str, Any]:
    return {"masterpieces": playground_store.list_masterpieces()}


@router.post("/decks/{thread_id}/playground/lanes/{lane_id}/masterpiece")
def save_lane_masterpiece(thread_id: str, lane_id: str) -> dict[str, Any]:
    _require_playground_base(thread_id)
    row = playground_store.get_lane(thread_id, lane_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown playground lane")
    try:
        masterpiece = playground_store.save_masterpiece(row["creator_prompt"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "masterpiece": masterpiece}


@router.delete("/masterpieces/{masterpiece_id}")
def delete_masterpiece(masterpiece_id: str) -> dict[str, Any]:
    if not playground_store.delete_masterpiece(masterpiece_id):
        raise HTTPException(status_code=404, detail="Unknown masterpiece")
    return {"ok": True}
