"""StateGraph wiring with SqliteSaver, interrupt() HITL gates, and Send fan-out.

Topology (matches the plan):

  ingest → outline_propose → [interrupt: structure]
                              ↓
                        outline → [interrupt: outline/playground]
                              ↓
                         style → [interrupt: style]
                                          ↓
                                       layout → [interrupt: layout]
                                                ↓
                                           consolidate
                                                ↓
                                 Send(html_one, i) × N  (parallel fan-out)
                                                ↓
                                              ready
                                                ⇄ edit_intent (loop)

Regenerate-from-stage uses LangGraph's native `update_state(..., as_node=...)` +
`invoke(None, config)` pattern; see api/history.py for the HTTP recipe.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from ..catalog.visual_presets import normalize_visual_style_preset_id, visual_style_preset_state
from .layout_overrides import apply_layout_overrides
from .nodes.advanced_chat import commit_advanced_chat_draft, init_advanced_chat_node
from .nodes.consolidate import consolidate_node
from .nodes.digest import digest_materials_node
from .nodes.edit import edit_intent_node
from .nodes.html_one import html_one_node
from .nodes.image_insert import has_image_insertion_opportunity
from .nodes.ingest import ingest_node
from .nodes.layout import layout_node
from .nodes.outline import outline_node, propose_structures_node
from .nodes.style import style_node
from .state import SlideState

DB_PATH = Path(os.getenv("OSZ_CHECKPOINT_DB", "./threads.sqlite"))


# ---------------------------------------------------------------------------
# HITL interrupt wrappers.
#
# Each returns a dict merged into state when the human responds.
# `interrupt(payload)` pauses the graph; the resume value replaces the call's
# return, which we then unpack.
# ---------------------------------------------------------------------------

def await_structure(state: SlideState) -> dict[str, Any]:
    resume = interrupt({
        "gate": "structure",
        "scenario_id": state.get("scenario_id"),
        "candidates": state.get("structure_candidates", []),
        "hint": "Pick scenario_id + structure_id to proceed.",
    })
    # Expected shape: {"scenario_id": "...", "structure_id": "..."}
    return {
        "scenario_id": resume.get("scenario_id", state.get("scenario_id")),
        "structure_id": resume["structure_id"],
        "current_stage": "outline",
    }


def await_advanced_chat(state: SlideState) -> dict[str, Any]:
    resume = interrupt({
        "gate": "advanced_chat",
        "messages": state.get("advanced_chat_messages", []),
        "draft": state.get("advanced_chat_draft", {}),
        "hint": (
            "Use /advanced_chat/stream to update the draft. Return "
            "{approved: true, draft: {...}} to continue to layout."
        ),
    })
    if not resume.get("approved"):
        raise ValueError("Advanced chat must be approved before continuing to layout.")
    return commit_advanced_chat_draft(
        resume.get("draft") or state.get("advanced_chat_draft") or {}
    )


def await_style_review(state: SlideState) -> dict[str, Any]:
    resume = interrupt({
        "gate": "style",
        "visual_style_md": state.get("visual_style_md"),
        "visual_style": state.get("visual_style"),
        "hint": "Return {approved: true} to continue, or {revise: 'free-text feedback'} to rerun.",
    })
    if resume.get("approved"):
        return {"current_stage": "layout"}
    # User wants revisions → loop back to style via a feedback preference
    feedback = resume.get("revise", "")
    return {
        "visual_style_preference": feedback,
        "current_stage": "style",
        "visual_style_md": "",
        "visual_style": {},
    }


def await_outline_review(state: SlideState) -> dict[str, Any]:
    resume = interrupt({
        "gate": "outline",
        "outline_md": state.get("outline_md"),
        "outline_slides": state.get("outline_slides"),
        "hint": (
            "Return {approved: true} to continue the normal flow, or "
            "{playground: true} to stop here and create creator playground lanes."
        ),
    })
    if resume.get("playground"):
        return {"current_stage": "playground"}
    return {"current_stage": "style"}


def await_layout_review(state: SlideState) -> dict[str, Any]:
    resume = interrupt({
        "gate": "layout",
        "layouts": state.get("layouts"),
        "visual_style_preset_id": state.get("visual_style_preset_id"),
        "hint": (
            "Per-slide override map, e.g. {'overrides': {3: 'radial', 5: 'timeline_horizontal'}, "
            "'approved': true}"
        ),
    })
    overrides: dict[int, str] = resume.get("overrides", {}) or {}
    layouts = apply_layout_overrides(state.get("layouts"), overrides)
    preset_changed = (
        normalize_visual_style_preset_id(resume.get("visual_style_preset_id"))
        != normalize_visual_style_preset_id(state.get("visual_style_preset_id"))
    )
    preset_update = visual_style_preset_state(resume.get("visual_style_preset_id"))
    if preset_changed:
        return {
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
        }
    if not resume.get("approved"):
        return {"layouts": layouts, **preset_update}
    return {"layouts": layouts, "current_stage": "consolidate", **preset_update}


# ---------------------------------------------------------------------------
# Fan-out conditional edge: from consolidate we Send one task per slide.
# ---------------------------------------------------------------------------

def fan_out_html(state: SlideState) -> list[Send]:
    brief = state.get("brief")
    if not brief:
        return []
    slides = brief["slides"]
    retry_only = {
        int(idx) for idx in (state.get("pending_html_retry_slides") or [])
    }
    if retry_only:
        slides = [slide for slide in slides if int(slide["slide_idx"]) in retry_only]
    return [Send("html_one", {"slide_idx": s["slide_idx"], "brief": brief}) for s in slides]


def post_html(state: SlideState) -> dict[str, Any]:
    brief = state.get("brief") or {}
    expected_slide_ids = [
        int(slide["slide_idx"]) for slide in (brief.get("slides") or [])
    ]
    rendered_slide_ids = {
        int(idx) for idx in (state.get("html_slides") or {}).keys()
    }
    failures = {
        int(item["slide_idx"]): dict(item)
        for item in (state.get("html_failures") or [])
    }

    for slide_idx in expected_slide_ids:
        if slide_idx not in rendered_slide_ids and slide_idx not in failures:
            failures[slide_idx] = {
                "slide_idx": slide_idx,
                "attempt_count": 0,
                "reason": "slide did not produce HTML",
                "finish_reason": None,
            }

    if len(rendered_slide_ids) == len(expected_slide_ids) and not failures:
        image_status = (
            "available" if has_image_insertion_opportunity(state) else "unavailable"
        )
        return {
            "current_stage": "ready",
            "html_failures": [],
            "pending_html_retry_slides": [],
            "image_insertion_status": image_status,
        }

    failed_slides = [failures[idx] for idx in sorted(failures)]
    resume = interrupt({
        "gate": "html",
        "failed_slides": failed_slides,
        "rendered_count": len(rendered_slide_ids),
        "expected_count": len(expected_slide_ids),
        "hint": "Retry failed slides.",
    })
    retry_slide_ids = [int(item["slide_idx"]) for item in failed_slides]
    if resume.get("retry_failed"):
        return {
            "current_stage": "html",
            "html_failures": [],
            "pending_html_retry_slides": retry_slide_ids,
        }
    return {
        "current_stage": "html",
        "pending_html_retry_slides": retry_slide_ids,
    }


def retry_html_node(_state: SlideState) -> dict[str, Any]:
    return {}


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph(checkpointer: SqliteSaver | None = None):
    g: StateGraph[SlideState, SlideState, SlideState] = StateGraph(SlideState)

    g.add_node("ingest", ingest_node)
    g.add_node("digest_materials", digest_materials_node)
    g.add_node("advanced_chat", init_advanced_chat_node)
    g.add_node("await_advanced_chat", await_advanced_chat)
    g.add_node("propose_structure", propose_structures_node)
    g.add_node("await_structure", await_structure)
    g.add_node("outline", outline_node)
    g.add_node("await_outline", await_outline_review)
    g.add_node("style", style_node)
    g.add_node("await_style", await_style_review)
    g.add_node("layout", layout_node)
    g.add_node("await_layout", await_layout_review)
    g.add_node("consolidate", consolidate_node)
    g.add_node("html_one", html_one_node)
    g.add_node("post_html", post_html)
    g.add_node("retry_html", retry_html_node)
    g.add_node("edit_intent", edit_intent_node)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "digest_materials")

    def after_digest(state: SlideState) -> str:
        return "advanced_chat" if state.get("agent_mode") == "advanced" else "propose_structure"

    g.add_conditional_edges(
        "digest_materials",
        after_digest,
        {"advanced_chat": "advanced_chat", "propose_structure": "propose_structure"},
    )
    g.add_edge("advanced_chat", "await_advanced_chat")
    g.add_edge("await_advanced_chat", "layout")
    g.add_edge("propose_structure", "await_structure")
    g.add_edge("await_structure", "outline")
    g.add_edge("outline", "await_outline")

    def outline_next(state: SlideState) -> str:
        return "__end__" if state.get("current_stage") == "playground" else "style"
    g.add_conditional_edges("await_outline", outline_next, {"style": "style", "__end__": END})

    g.add_edge("style", "await_style")

    # Style gate loops back to style when user requests revisions.
    def style_next(state: SlideState) -> str:
        return "style" if state.get("current_stage") == "style" else "layout"
    g.add_conditional_edges("await_style", style_next, {"style": "style", "layout": "layout"})

    g.add_edge("layout", "await_layout")

    # Layout gate loops back to layout when not approved.
    def layout_next(state: SlideState) -> str:
        if state.get("current_stage") == "style":
            return "style"
        return "consolidate" if state.get("current_stage") == "consolidate" else "layout"
    g.add_conditional_edges(
        "await_layout",
        layout_next,
        {"style": "style", "layout": "layout", "consolidate": "consolidate"},
    )

    g.add_conditional_edges("consolidate", fan_out_html, ["html_one"])
    g.add_edge("html_one", "post_html")
    def html_next(state: SlideState) -> str:
        return "ready" if state.get("current_stage") == "ready" else "retry_html"
    g.add_conditional_edges("post_html", html_next, {"ready": END, "retry_html": "retry_html"})
    g.add_conditional_edges("retry_html", fan_out_html, ["html_one"])
    g.add_edge("edit_intent", END)   # edit_intent merely populates pending_edit_ops

    if checkpointer is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        checkpointer = SqliteSaver(sqlite3.connect(str(DB_PATH), check_same_thread=False))

    return g.compile(checkpointer=checkpointer)


# Module-level singleton (lazy built to avoid DB connect at import).
_compiled = None


def get_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled
