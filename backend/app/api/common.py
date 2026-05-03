"""Shared FastAPI helpers: graph accessor, state serializers, config builders."""

from __future__ import annotations

import logging
import shutil
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from ..artifacts import store
from ..catalog.visual_presets import normalize_visual_style_preset_id, visual_style_preset_state
from ..graph.graph import get_graph
from ..graph.layout_overrides import apply_layout_overrides
from ..graph.nodes.advanced_chat import commit_advanced_chat_draft

log = logging.getLogger(__name__)


def config_for(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def graph() -> CompiledStateGraph:
    return get_graph()


def _html_failure_payload(values: dict[str, Any]) -> dict[str, Any] | None:
    brief = values.get("brief") or {}
    expected_slide_ids = [
        int(slide["slide_idx"]) for slide in (brief.get("slides") or [])
    ]
    if not expected_slide_ids:
        return None
    rendered_slide_ids = {
        int(idx) for idx in (values.get("html_slides") or {}).keys()
    }
    failures = {
        int(item["slide_idx"]): dict(item)
        for item in (values.get("html_failures") or [])
    }
    for slide_idx in expected_slide_ids:
        if slide_idx not in rendered_slide_ids and slide_idx not in failures:
            failures[slide_idx] = {
                "slide_idx": slide_idx,
                "attempt_count": 0,
                "reason": "slide did not produce HTML",
                "finish_reason": None,
            }
    if not failures:
        return None
    failed_slides = [failures[idx] for idx in sorted(failures)]
    return {
        "gate": "html",
        "failed_slides": failed_slides,
        "rendered_count": len(rendered_slide_ids),
        "expected_count": len(expected_slide_ids),
        "hint": "Retry failed slides.",
    }


def synthetic_interrupt(values: dict[str, Any] | None) -> dict[str, Any] | None:
    """Rebuild a HITL gate when a stream was closed between a stage and its gate.

    LangGraph checkpoints can be left with ``current_stage == "await_*"`` but no
    pending interrupt if the client disconnects after the generating node commits
    and before the interrupt wrapper runs. The graph state still has everything
    needed to render the review UI, so expose a synthetic gate and let the resume
    endpoints convert the user's response into the same patch the wrapper would
    have returned.
    """
    if not values:
        return None
    stage = values.get("current_stage")
    if stage == "await_structure":
        return {
            "gate": "structure",
            "scenario_id": values.get("scenario_id"),
            "candidates": values.get("structure_candidates", []),
            "hint": "Pick scenario_id + structure_id to proceed.",
        }
    if stage == "advanced_chat":
        return {
            "gate": "advanced_chat",
            "messages": values.get("advanced_chat_messages", []),
            "draft": values.get("advanced_chat_draft", {}),
            "hint": (
                "Use /advanced_chat/stream to update the draft. Return "
                "{approved: true, draft: {...}} to continue to layout."
            ),
        }
    if stage == "await_outline":
        return {
            "gate": "outline",
            "outline_md": values.get("outline_md"),
            "outline_slides": values.get("outline_slides"),
            "hint": (
                "Return {approved: true} to continue the normal flow, or "
                "{playground: true} to stop here and create creator playground lanes."
            ),
        }
    if stage == "await_style":
        return {
            "gate": "style",
            "visual_style_md": values.get("visual_style_md"),
            "visual_style": values.get("visual_style"),
            "hint": (
                "Return {approved: true} to continue, or "
                "{revise: 'free-text feedback'} to rerun."
            ),
        }
    if stage == "await_layout":
        return {
            "gate": "layout",
            "layouts": values.get("layouts"),
            "visual_style_preset_id": values.get("visual_style_preset_id"),
            "hint": (
                "Per-slide override map, e.g. "
                "{'overrides': {3: 'radial', 5: 'timeline_horizontal'}, 'approved': true}"
            ),
        }
    if stage == "html":
        return _html_failure_payload(values)
    return None


def _synthetic_gate_node(values: dict[str, Any] | None) -> str | None:
    if not values:
        return None
    stage = values.get("current_stage")
    return {
        "await_structure": "await_structure",
        "advanced_chat": "await_advanced_chat",
        "await_outline": "await_outline",
        "await_style": "await_style",
        "await_layout": "await_layout",
        "html": "post_html",
    }.get(stage)


def _resume_patch_for_synthetic_gate(
    values: dict[str, Any],
    resume: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    stage = values.get("current_stage")
    if stage == "await_structure":
        return (
            "await_structure",
            {
                "scenario_id": resume.get("scenario_id", values.get("scenario_id")),
                "structure_id": resume["structure_id"],
                "current_stage": "outline",
            },
        )
    if stage == "advanced_chat":
        if not resume.get("approved"):
            raise ValueError("Advanced chat must be approved before continuing to layout.")
        return (
            "await_advanced_chat",
            commit_advanced_chat_draft(
                resume.get("draft") or values.get("advanced_chat_draft") or {}
            ),
        )
    if stage == "await_outline":
        return (
            "await_outline",
            {"current_stage": "playground" if resume.get("playground") else "style"},
        )
    if stage == "await_style":
        if resume.get("approved"):
            return ("await_style", {"current_stage": "layout"})
        return (
            "await_style",
            {
                "visual_style_preference": resume.get("revise", ""),
                "current_stage": "style",
                "visual_style_md": "",
                "visual_style": {},
            },
        )
    if stage == "await_layout":
        overrides: dict[int, str] = resume.get("overrides", {}) or {}
        layouts = apply_layout_overrides(values.get("layouts"), overrides)
        preset_changed = (
            normalize_visual_style_preset_id(resume.get("visual_style_preset_id"))
            != normalize_visual_style_preset_id(values.get("visual_style_preset_id"))
        )
        preset_update = visual_style_preset_state(resume.get("visual_style_preset_id"))
        if preset_changed:
            return (
                "await_layout",
                {
                    "current_stage": "style",
                    "visual_style_md": "",
                    "visual_style": {},
                    "layouts": [],
                    "consolidated_brief_md": "",
                    "brief": {},
                    "html_slides": {},
                    "html_failures": [],
                    "pending_html_retry_slides": [],
                    **preset_update,
                },
            )
        if not resume.get("approved"):
            return ("await_layout", {"layouts": layouts, **preset_update})
        return (
            "await_layout",
            {"layouts": layouts, "current_stage": "consolidate", **preset_update},
        )
    if stage == "html":
        gate = _html_failure_payload(values)
        if not gate:
            return None
        retry_slide_ids = [int(item["slide_idx"]) for item in gate["failed_slides"]]
        if resume.get("retry_failed"):
            return (
                "post_html",
                {
                    "current_stage": "html",
                    "html_failures": [],
                    "pending_html_retry_slides": retry_slide_ids,
                },
            )
        return (
            "post_html",
            {
                "current_stage": "html",
                "pending_html_retry_slides": retry_slide_ids,
            },
        )
    return None


def resume_synthetic_interrupt(
    thread_id: str,
    resume: dict[str, Any],
) -> dict[str, Any] | None:
    snap = graph().get_state(config_for(thread_id))
    if not snap or snap.interrupts:
        return None
    values = snap.values or {}
    gate_node = _synthetic_gate_node(values)
    if snap.next and tuple(snap.next) != (gate_node,):
        return None
    if synthetic_interrupt(values) is None:
        return None
    patch = _resume_patch_for_synthetic_gate(values, resume)
    if patch is None:
        return None
    as_node, update = patch
    return graph().update_state(snap.config, update, as_node=as_node)


def delete_thread(thread_id: str) -> None:
    """Drop a thread's checkpoints + on-disk mirror. Safe no-op if absent.

    Used by the create endpoints when a hard failure (e.g. ingest ValueError)
    happens before any successful checkpoint, to prevent zombie threads from
    appearing in GET /decks. Do not call from resume paths — a mid-pipeline
    failure there is recoverable via history rewind.
    """
    try:
        checkpointer = cast(BaseCheckpointSaver, graph().checkpointer)
        checkpointer.delete_thread(thread_id)
    except Exception:
        log.exception("failed to delete checkpoint rows for %s", thread_id)
    artifact_dir = store.ROOT / thread_id
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir, ignore_errors=True)


def current_state(thread_id: str, *, source_thread_id: str | None = None) -> dict[str, Any]:
    g = graph()
    snap = g.get_state(config_for(thread_id))
    next_nodes = list(snap.next)
    interrupts = [i.value if hasattr(i, "value") else i for i in (snap.interrupts or [])]
    values = dict(snap.values or {})
    if (
        next_nodes == ["await_advanced_chat"]
        and (
            values.get("agent_mode") == "advanced"
            or any(
                isinstance(item, dict) and item.get("gate") == "advanced_chat"
                for item in interrupts
            )
        )
    ):
        values["current_stage"] = "advanced_chat"
    gate_node = _synthetic_gate_node(values)
    if not interrupts and (not next_nodes or next_nodes == [gate_node]):
        rebuilt = synthetic_interrupt(values)
        if rebuilt is not None:
            interrupts = [rebuilt]
    state = {
        "thread_id": thread_id,
        "checkpoint_id": snap.config["configurable"].get("checkpoint_id"),
        "values": values,
        "next": next_nodes,
        "interrupts": interrupts,
        "created_at": getattr(snap, "created_at", None),
    }
    if source_thread_id is not None:
        state["source_thread_id"] = source_thread_id
    return state


def mirror_to_disk(thread_id: str) -> None:
    snap = graph().get_state(config_for(thread_id))
    if snap and snap.values:
        store.write_slide_mirrors(thread_id, snap.values)


__all__ = ["config_for", "graph", "current_state", "delete_thread", "mirror_to_disk"]
