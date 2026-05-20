"""Creator playground endpoints.

The base deck stops after visual style is locked. Each lane is a separate
LangGraph thread cloned from that style-locked checkpoint and then resumed
through layout and HTML with its own creator prompt.
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..llm import stream as stream_module
from ..llm.models import (
    lane_model_options,
    normalize_lane_model_overrides,
    normalize_lane_thinking_effort_overrides,
    runtime_model_options,
)
from ..llm.runtime_config import RuntimeLLMConfig, runtime_config_from_request, use_runtime_config
from . import playground_store
from .common import config_for, current_state, delete_thread, graph
from .history import _clone_checkpoint_lineage
from .owners import Owner, assign_deck_to_owner, current_owner, require_deck_owner
from .streaming import SSE_HEADERS, _sse, _stream_graph

router = APIRouter()
CREATOR_PLAYGROUND_MODEL_STAGES = ("layout", "html")


class CreateLaneBody(BaseModel):
    creator_prompt: str = ""
    model_overrides: dict[str, str] | None = None
    thinking_effort_overrides: dict[str, str] | None = None


class UpdateLaneBody(BaseModel):
    lane_name: str = ""


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
        "lane_name": row["lane_name"],
        "created_at": row["created_at"],
        "state": state,
    }


def _require_playground_base(thread_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    snap = graph().get_state(config_for(thread_id))  # type: ignore[arg-type]
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown deck")
    if snap.values.get("current_stage") != "playground":
        raise HTTPException(
            status_code=409,
            detail="Enable Creator Playground after visual style is locked before creating lanes.",
        )
    if not snap.values.get("visual_style"):
        raise HTTPException(
            status_code=409,
            detail="Creator Playground requires a locked visual style.",
        )
    return snap.values, snap.config


def _prepare_lane(thread_id: str, body: CreateLaneBody) -> tuple[dict[str, Any], dict[str, Any]]:
    base_values, target_cfg = _require_playground_base(thread_id)
    try:
        model_overrides = normalize_lane_model_overrides(
            body.model_overrides,
            allowed_stages=CREATOR_PLAYGROUND_MODEL_STAGES,
        )
        thinking_effort_overrides = normalize_lane_thinking_effort_overrides(
            body.thinking_effort_overrides,
            allowed_stages=CREATOR_PLAYGROUND_MODEL_STAGES,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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
                "lane_model_overrides": model_overrides or None,
                "lane_thinking_effort_overrides": thinking_effort_overrides or None,
                "current_stage": "layout",
            },
            as_node="await_style",
        )
    except Exception:
        playground_store.delete_lane_record(thread_id, lane["lane_id"])
        raise

    return lane, lane_cfg


@router.get("/playground/model-options")
def get_playground_model_options() -> dict[str, Any]:
    return lane_model_options(CREATOR_PLAYGROUND_MODEL_STAGES)


@router.get("/runtime/model-options")
def get_runtime_model_options() -> dict[str, Any]:
    return runtime_model_options()


@router.get("/decks/{thread_id}/playground/lanes")
def list_playground_lanes(
    thread_id: str,
    owner: Owner = Depends(current_owner),
) -> dict[str, Any]:
    require_deck_owner(thread_id, owner)
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
    owner: Owner = Depends(current_owner),
    runtime_config: RuntimeLLMConfig = Depends(runtime_config_from_request),
) -> StreamingResponse:
    require_deck_owner(thread_id, owner)
    lane, lane_cfg = _prepare_lane(thread_id, body)
    assign_deck_to_owner(lane["lane_thread_id"], owner)

    def gen() -> Iterable[str]:
        with use_runtime_config(runtime_config):
            yield _sse({
                "type": "thread",
                "thread_id": lane["lane_thread_id"],
                "parent_thread_id": thread_id,
                "lane_id": lane["lane_id"],
            })
            yield _sse({"type": "lane", "lane": _lane_response(lane)})
            yield from _stream_graph(None, lane_cfg, lane["lane_thread_id"])

    return StreamingResponse(gen(), headers=SSE_HEADERS)


@router.delete("/decks/{thread_id}/playground/lanes/{lane_id}")
def delete_playground_lane(
    thread_id: str,
    lane_id: str,
    owner: Owner = Depends(current_owner),
) -> dict[str, Any]:
    require_deck_owner(thread_id, owner)
    _require_playground_base(thread_id)
    row = playground_store.delete_lane_record(thread_id, lane_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown playground lane")

    stream_module.cancel_lane(row["lane_thread_id"])
    deleted_lane = _lane_response(row)
    delete_thread(row["lane_thread_id"])
    return {
        "ok": True,
        "lane": deleted_lane,
        "thread_id": row["lane_thread_id"],
        "deleted_thread_ids": [row["lane_thread_id"]],
    }


@router.patch("/decks/{thread_id}/playground/lanes/{lane_id}")
def update_playground_lane(
    thread_id: str,
    lane_id: str,
    body: UpdateLaneBody,
    owner: Owner = Depends(current_owner),
) -> dict[str, Any]:
    require_deck_owner(thread_id, owner)
    _require_playground_base(thread_id)
    row = playground_store.update_lane_name(thread_id, lane_id, body.lane_name)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown playground lane")
    return {"ok": True, "lane": _lane_response(row)}


@router.get("/masterpieces")
def list_masterpieces(owner: Owner = Depends(current_owner)) -> dict[str, Any]:
    return {"masterpieces": playground_store.list_masterpieces(owner.owner_hash)}


@router.post("/decks/{thread_id}/playground/lanes/{lane_id}/masterpiece")
def save_lane_masterpiece(
    thread_id: str,
    lane_id: str,
    owner: Owner = Depends(current_owner),
) -> dict[str, Any]:
    require_deck_owner(thread_id, owner)
    _require_playground_base(thread_id)
    row = playground_store.get_lane(thread_id, lane_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown playground lane")
    try:
        masterpiece = playground_store.save_masterpiece(owner.owner_hash, row["creator_prompt"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "masterpiece": masterpiece}


@router.delete("/masterpieces/{masterpiece_id}")
def delete_masterpiece(
    masterpiece_id: str,
    owner: Owner = Depends(current_owner),
) -> dict[str, Any]:
    if not playground_store.delete_masterpiece(owner.owner_hash, masterpiece_id):
        raise HTTPException(status_code=404, detail="Unknown masterpiece")
    return {"ok": True}
