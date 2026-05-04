"""HITL resume endpoint.

POST /decks/{thread_id}/resume
    Body is the value the human supplies to the open interrupt() call. Depending on
    the gate the graph is paused at (structure | style | layout), the shape differs:

      structure: {"scenario_id": "...", "structure_id": "..."}
      style:     {"approved": true} OR {"revise": "feedback string"}
      layout:    {"approved": true, "overrides": {"3": "radial"}, "visual_style_preset_id": "..."}
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from langgraph.types import Command

from .common import config_for, current_state, graph, mirror_to_disk, resume_synthetic_interrupt

router = APIRouter()


@router.post("/decks/{thread_id}/resume")
def resume_deck(thread_id: str, body: dict[str, Any]) -> dict[str, Any]:
    snap = graph().get_state(config_for(thread_id))  # type: ignore[arg-type]
    if not snap:
        raise HTTPException(status_code=404, detail="Unknown deck")
    try:
        if snap.interrupts:
            graph().invoke(Command(resume=body), config_for(thread_id))  # type: ignore[arg-type]
        else:
            cfg = resume_synthetic_interrupt(thread_id, body)
            if cfg is None:
                raise HTTPException(status_code=409, detail="No pending interrupt to resume")
            graph().invoke(None, cfg)  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    mirror_to_disk(thread_id)
    return current_state(thread_id)
