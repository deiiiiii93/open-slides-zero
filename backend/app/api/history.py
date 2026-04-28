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

import sqlite3
import uuid
from typing import Any
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from langgraph.types import Command
from pydantic import BaseModel, Field

from ..artifacts import store
from ..catalog.visual_presets import normalize_visual_style_preset_id, visual_style_preset_state
from ..graph import graph as graph_module
from ..graph.layout_overrides import apply_layout_overrides
from .common import config_for, current_state, graph, mirror_to_disk

router = APIRouter()


# Downstream-field invalidation per stage.
# Order matters — keys appearing after from_stage get nulled.
_DOWNSTREAM_FIELDS: dict[str, list[str]] = {
    "outline":      ["outline_md", "outline_slides", "visual_style_md", "visual_style",
                     "layouts", "consolidated_brief_md", "brief", "html_slides",
                     "html_failures", "pending_html_retry_slides"],
    "style":        ["visual_style_md", "visual_style",
                     "layouts", "consolidated_brief_md", "brief", "html_slides",
                     "html_failures", "pending_html_retry_slides"],
    "layout":       ["layouts", "consolidated_brief_md", "brief", "html_slides",
                     "html_failures", "pending_html_retry_slides"],
    "consolidate":  ["consolidated_brief_md", "brief", "html_slides",
                     "html_failures", "pending_html_retry_slides"],
    "html":         ["html_slides", "html_failures", "pending_html_retry_slides"],
    "image_only":   [],  # no state invalidation; only prompt-hint change
}


def _empty_for(field: str) -> Any:
    if field.endswith("_md"):
        return ""
    if field == "html_slides":
        return {}
    if field == "brief":
        return {}
    if field == "html_failures" or field == "pending_html_retry_slides":
        return []
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
    return _find_checkpoint_with_next(g, cfg, target_node)


def _find_checkpoint_with_next(g: Any, cfg: dict[str, Any], node: str) -> dict[str, Any] | None:
    """Walk the state history newest→oldest; return the most recent checkpoint
    whose `next` queue contains `node`."""
    for snap in g.get_state_history(cfg):
        if node in (snap.next or ()):
            return snap.config
    return None


def _default_fork_name(values: dict[str, Any], thread_id: str) -> str:
    return f"{values.get('deck_name') or thread_id} (fork)"


def _downstream_clear_patch(from_stage: str) -> dict[str, Any]:
    return {field: _empty_for(field) for field in _DOWNSTREAM_FIELDS[from_stage]}


def _clone_checkpoint_lineage(
    source_thread_id: str,
    target_cfg: dict[str, Any],
    new_thread_id: str,
) -> dict[str, Any]:
    checkpoint_ns = target_cfg["configurable"].get("checkpoint_ns", "")
    checkpoint_id = target_cfg["configurable"].get("checkpoint_id")
    if not checkpoint_id:
        raise HTTPException(status_code=409, detail="Target checkpoint_id missing.")

    lineage: list[tuple[str, str | None, str | None, bytes, bytes | None]] = []
    conn = sqlite3.connect(str(graph_module.DB_PATH))
    try:
        current_id = checkpoint_id
        while current_id:
            row = conn.execute(
                """
                SELECT checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata
                FROM checkpoints
                WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                """,
                (source_thread_id, checkpoint_ns, current_id),
            ).fetchone()
            if row is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Missing checkpoint lineage row for {current_id}.",
                )
            lineage.append(row)
            current_id = row[1]

        with conn:
            for row_checkpoint_id, parent_checkpoint_id, row_type, checkpoint, metadata in reversed(lineage):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO checkpoints
                    (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_thread_id,
                        checkpoint_ns,
                        row_checkpoint_id,
                        parent_checkpoint_id,
                        row_type,
                        checkpoint,
                        metadata,
                    ),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO writes
                    (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value)
                    SELECT ?, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value
                    FROM writes
                    WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                    """,
                    (new_thread_id, source_thread_id, checkpoint_ns, row_checkpoint_id),
                )
    finally:
        conn.close()

    return {
        "configurable": {
            "thread_id": new_thread_id,
            "checkpoint_ns": checkpoint_ns,
            "checkpoint_id": checkpoint_id,
        }
    }


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

    # 2. Overlay user patch onto that historical checkpoint and explicitly clear
    #    stale downstream state. LangGraph reducers do not infer invalidation
    #    from rewinds, so fields like `brief` and `html_slides` must be reset.
    user_patch = dict(patch or {})
    rewind_patch = {**_downstream_clear_patch(from_stage), **user_patch}
    if rewind_patch:
        # Preserve the target checkpoint_id so update_state forks from there,
        # not from the latest state.
        new_cfg = g.update_state(target_cfg, rewind_patch)  # type: ignore[arg-type]
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


def _fork_from_review(
    thread_id: str,
    review_stage: Literal["structure", "style", "layout"],
    *,
    scenario_id: str | None = None,
    structure_id: str | None = None,
    feedback: str | None = None,
    overrides: dict[int | str, str] | None = None,
    visual_style_preset_id: str | None = None,
    deck_name: str | None = None,
) -> dict[str, Any]:
    g = graph()
    cfg = config_for(thread_id)
    snap = g.get_state(cfg)  # type: ignore[arg-type]
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown deck")
    if snap.values.get("current_stage") != "ready":
        raise HTTPException(status_code=409, detail="Deck must be in ready state to fork from review.")

    new_thread_id = uuid.uuid4().hex[:12]
    fork_name = deck_name or _default_fork_name(snap.values, thread_id)
    patch: dict[str, Any] = {
        "thread_id": new_thread_id,
        "deck_name": fork_name,
    }

    if review_stage == "structure":
        if not scenario_id or not structure_id:
            raise HTTPException(status_code=400, detail="scenario_id and structure_id are required.")
        target_stage = "outline"
        patch.update({
            "scenario_id": scenario_id,
            "structure_id": structure_id,
        })
    elif review_stage == "style":
        if not feedback or not feedback.strip():
            raise HTTPException(status_code=400, detail="feedback is required.")
        target_stage = "style"
        patch["visual_style_preference"] = feedback.strip()
    else:
        preset_update = visual_style_preset_state(visual_style_preset_id)
        preset_changed = (
            normalize_visual_style_preset_id(visual_style_preset_id)
            != normalize_visual_style_preset_id(snap.values.get("visual_style_preset_id"))
        )
        if preset_changed:
            source_target_cfg = _find_prestage_checkpoint(g, cfg, "style")
            if source_target_cfg is None:
                raise HTTPException(
                    status_code=409,
                    detail="No prior checkpoint found with node 'style' pending.",
                )
            patch = {
                **_downstream_clear_patch("style"),
                **patch,
                **preset_update,
                "current_stage": "style",
            }
            new_target_cfg = _clone_checkpoint_lineage(thread_id, source_target_cfg, new_thread_id)
            new_cfg = g.update_state(new_target_cfg, patch)  # type: ignore[arg-type]
            g.invoke(None, new_cfg)  # type: ignore[arg-type]
            mirror_to_disk(new_thread_id)
            return current_state(new_thread_id, source_thread_id=thread_id)

        source_target_cfg = _find_checkpoint_with_next(g, cfg, "await_layout")
        if source_target_cfg is None:
            raise HTTPException(
                status_code=409,
                detail="No prior checkpoint found with node 'await_layout' pending.",
            )
        patch.update(_downstream_clear_patch("consolidate"))
        patch["layouts"] = apply_layout_overrides(snap.values.get("layouts"), overrides)
        patch["current_stage"] = "await_layout"
        patch.update(preset_update)

        new_target_cfg = _clone_checkpoint_lineage(thread_id, source_target_cfg, new_thread_id)
        g.update_state(new_target_cfg, patch)  # type: ignore[arg-type]
        mirror_to_disk(new_thread_id)
        return current_state(new_thread_id, source_thread_id=thread_id)

    source_target_cfg = _find_prestage_checkpoint(g, cfg, target_stage)
    if source_target_cfg is None:
        raise HTTPException(
            status_code=409,
            detail=f"No prior checkpoint found with node '{_STAGE_TO_NODE.get(target_stage)}' pending.",
        )

    patch = {**_downstream_clear_patch(target_stage), **patch}
    new_target_cfg = _clone_checkpoint_lineage(thread_id, source_target_cfg, new_thread_id)
    new_cfg = g.update_state(new_target_cfg, patch)  # type: ignore[arg-type]
    g.invoke(None, new_cfg)  # type: ignore[arg-type]
    if review_stage == "structure":
        fork_snap = g.get_state(config_for(new_thread_id))  # type: ignore[arg-type]
        interrupts = list(fork_snap.interrupts or []) if fork_snap else []
        if interrupts:
            payload = interrupts[0]
            value = payload.value if hasattr(payload, "value") else payload
            if isinstance(value, dict) and value.get("gate") == "outline":
                g.invoke(Command(resume={"approved": True}), config_for(new_thread_id))  # type: ignore[arg-type]
    mirror_to_disk(new_thread_id)
    return current_state(new_thread_id, source_thread_id=thread_id)


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


class RegenerateBody(BaseModel):
    from_stage: str
    patch: dict[str, Any] | None = None
    affected_slides: list[int] | None = None


class ForkFromStructureBody(BaseModel):
    review_stage: Literal["structure"]
    scenario_id: str
    structure_id: str
    deck_name: str | None = None


class ForkFromStyleBody(BaseModel):
    review_stage: Literal["style"]
    feedback: str
    deck_name: str | None = None


class ForkFromLayoutBody(BaseModel):
    review_stage: Literal["layout"]
    overrides: dict[int | str, str] = Field(default_factory=dict)
    visual_style_preset_id: str | None = None
    deck_name: str | None = None


ForkFromReviewBody = Annotated[
    ForkFromStructureBody | ForkFromStyleBody | ForkFromLayoutBody,
    Field(discriminator="review_stage"),
]


@router.post("/decks/{thread_id}/regenerate")
def regenerate(thread_id: str, body: RegenerateBody) -> dict[str, Any]:
    return _regenerate_from(
        thread_id,
        body.from_stage,
        body.patch,
        affected_slides=body.affected_slides,
    )


@router.post("/decks/{thread_id}/fork_from_review")
def fork_from_review(thread_id: str, body: ForkFromReviewBody) -> dict[str, Any]:
    try:
        if body.review_stage == "structure":
            return _fork_from_review(
                thread_id,
                "structure",
                scenario_id=body.scenario_id,
                structure_id=body.structure_id,
                deck_name=body.deck_name,
            )
        if body.review_stage == "style":
            return _fork_from_review(
                thread_id,
                "style",
                feedback=body.feedback,
                deck_name=body.deck_name,
            )
        return _fork_from_review(
            thread_id,
            "layout",
            overrides=body.overrides,
            visual_style_preset_id=body.visual_style_preset_id,
            deck_name=body.deck_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
