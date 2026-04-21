"""History + regenerate-from-stage.

Implements the exact recipe from the plan:
  1. Find the checkpoint at the target stage.
  2. Build a patch that explicitly nulls downstream fields.
  3. update_state(..., as_node=from_stage) → creates a fork checkpoint.
  4. invoke(None, new_cfg) → resumes forward from that point.
  5. Re-fetch latest checkpoint_id (LangGraph #4987 workaround).
  6. Invalidate file-mirror for downstream stages.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..artifacts import store
from .common import config_for, current_state, graph, mirror_to_disk

router = APIRouter()


# Downstream-field invalidation per stage.
# Order matters — keys appearing after from_stage get nulled.
_DOWNSTREAM_FIELDS: dict[str, list[str]] = {
    "outline":      ["outline_md", "outline_slides", "visual_style_md", "visual_style",
                     "layouts", "consolidated_brief_md", "html_slides"],
    "style":        ["visual_style_md", "visual_style",
                     "layouts", "consolidated_brief_md", "html_slides"],
    "layout":       ["layouts", "consolidated_brief_md", "html_slides"],
    "consolidate":  ["consolidated_brief_md", "html_slides"],
    "html":         ["html_slides"],
    "image_only":   [],  # no state invalidation; only prompt-hint change
}


def _empty_for(field: str) -> Any:
    if field.endswith("_md"):
        return ""
    if field == "html_slides":
        return {}
    if field.endswith("_slides") or field == "layouts":
        return []
    if field == "visual_style":
        return {}
    return None


# Map from from_stage → the graph node name that produces that stage's output.
# When regenerating, we rewind to a checkpoint whose `next` queue contains this
# node, so invoking forward actually re-runs the node (not just replays the edge
# after it, which is what update_state(as_node=...) would do).
_STAGE_TO_NODE: dict[str, str] = {
    "outline":     "outline",
    "style":       "style",
    "layout":      "layout",
    "consolidate": "consolidate",
    "html":        "consolidate",  # html is a fan-out from consolidate
}


def _find_prestage_checkpoint(g: Any, cfg: dict[str, Any], stage: str) -> dict[str, Any] | None:
    """Walk the state history newest→oldest; return the config of the most recent
    checkpoint whose `next` queue contains the node that produces `stage`."""
    target_node = _STAGE_TO_NODE.get(stage)
    if not target_node:
        return None
    for snap in g.get_state_history(cfg):
        if target_node in (snap.next or ()):
            return snap.config
    return None


def _regenerate_from(
    thread_id: str,
    from_stage: str,
    patch: dict[str, Any] | None = None,
    *,
    affected_slides: list[int] | None = None,
) -> dict[str, Any]:
    g = graph()
    cfg = config_for(thread_id)
    snap = g.get_state(cfg)  # type: ignore[arg-type]
    if not snap:
        raise HTTPException(status_code=404, detail="Unknown deck")

    if from_stage not in _DOWNSTREAM_FIELDS:
        raise HTTPException(status_code=400, detail=f"Bad from_stage: {from_stage}")

    # 1. Find the checkpoint right BEFORE the target node ran — invoking from
    #    there will actually re-execute the node.
    target_cfg = _find_prestage_checkpoint(g, cfg, from_stage)
    if target_cfg is None:
        raise HTTPException(
            status_code=409,
            detail=f"No prior checkpoint found with node '{_STAGE_TO_NODE.get(from_stage)}' pending.",
        )

    # 2. Overlay user patch (if any) onto that historical checkpoint. We do NOT
    #    null downstream fields here — the target node will overwrite them as
    #    it re-executes, and nulling up-front removes information the node may
    #    need (e.g. ripping out `layouts` would starve consolidate).
    user_patch = dict(patch or {})
    if user_patch:
        # Preserve the target checkpoint_id so update_state forks from there,
        # not from the latest state.
        new_cfg = g.update_state(target_cfg, user_patch)  # type: ignore[arg-type]
    else:
        new_cfg = target_cfg

    # 3. HTML-subset edits: pre-populate preserved slides so the Send fan-out
    #    only regenerates the affected ones (the html_one nodes we care about
    #    re-run unconditionally; preserved slides survive via the reducer).
    if from_stage == "html" and affected_slides:
        current_html = snap.values.get("html_slides") or {}
        preserved = {
            int(k): v for k, v in current_html.items() if int(k) not in affected_slides
        }
        if preserved:
            new_cfg = g.update_state(new_cfg, {"html_slides": preserved})  # type: ignore[arg-type]

    # 4. Resume forward from the historical checkpoint — the target node runs
    #    fresh, everything downstream re-executes.
    g.invoke(None, new_cfg)  # type: ignore[arg-type]

    # 5. File-mirror invalidation + fresh write.
    store.invalidate_downstream(thread_id, from_stage)
    mirror_to_disk(thread_id)

    return {"ok": True, "from_stage": from_stage, "state": current_state(thread_id)}


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


class RegenerateBody(BaseModel):
    from_stage: str
    patch: dict[str, Any] | None = None
    affected_slides: list[int] | None = None


@router.post("/decks/{thread_id}/regenerate")
def regenerate(thread_id: str, body: RegenerateBody) -> dict[str, Any]:
    return _regenerate_from(
        thread_id,
        body.from_stage,
        body.patch,
        affected_slides=body.affected_slides,
    )


@router.get("/decks/{thread_id}/history")
def history(thread_id: str) -> dict[str, Any]:
    g = graph()
    cfg = config_for(thread_id)
    snaps = list(g.get_state_history(cfg))  # type: ignore[arg-type]
    if not snaps:
        raise HTTPException(status_code=404, detail="Unknown deck")
    items = []
    for snap in snaps:
        items.append({
            "checkpoint_id": snap.config["configurable"].get("checkpoint_id"),
            "stage": (snap.values or {}).get("current_stage"),
            "next": list(snap.next),
            "created_at": getattr(snap, "created_at", None),
            "has_interrupt": bool(snap.interrupts),
        })
    return {"thread_id": thread_id, "history": items}


@router.post("/decks/{thread_id}/rewind")
def rewind(thread_id: str, body: dict[str, str]) -> dict[str, Any]:
    """Jump back to an earlier checkpoint id (full conversation reload)."""
    checkpoint_id = body.get("checkpoint_id")
    if not checkpoint_id:
        raise HTTPException(status_code=400, detail="checkpoint_id required")
    cfg = {"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id}}
    g = graph()
    snap = g.get_state(cfg)  # type: ignore[arg-type]
    if not snap:
        raise HTTPException(status_code=404, detail="Unknown checkpoint")
    # Running invoke(None, cfg) from this checkpoint forks history and resumes.
    g.invoke(None, cfg)  # type: ignore[arg-type]
    mirror_to_disk(thread_id)
    return current_state(thread_id)
