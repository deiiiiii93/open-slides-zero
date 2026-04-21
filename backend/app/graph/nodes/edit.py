"""Comment → edit operations classifier.

Returns a LIST of edit-ops (multi-intent safe). The orchestrator then collapses
to the earliest target_stage, merges patch fragments, and regenerates from there.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ...llm import zenmux
from ...llm.models import get_model


_ALLOWED = Literal["outline", "style", "layout", "html", "image_only"]


class _EditOp(BaseModel):
    target_stage: _ALLOWED
    rationale: str
    affected_slides: list[int] = Field(default_factory=list)
    patch_fragment: dict[str, Any] = Field(default_factory=dict)


class _EditOps(BaseModel):
    ops: list[_EditOp] = Field(min_length=1)


_STAGE_ORDER = ["outline", "style", "layout", "html", "image_only"]


def edit_intent_node(state: dict[str, Any]) -> dict[str, Any]:
    comments = [c for c in state.get("comments", []) if not c.get("resolved")]
    if not comments:
        return {}

    outline_md = state.get("outline_md", "")
    visual_style_md = state.get("visual_style_md", "")
    layouts_summary = [
        {"slide_idx": l["slide_idx"], "title": l.get("title"), "pattern": l.get("pattern")}
        for l in (state.get("layouts") or [])
    ]

    system = (
        "You classify user comments on slide decks into edit operations. Each comment "
        "may produce 1 or more ops. Allowed target_stage values: outline, style, layout, "
        "html, image_only. Use 'html' when only the slide's HTML needs re-rendering "
        "(wording tweak, element reposition). Use 'image_only' when only a placeholder "
        "image prompt needs to change. 'layout' only when the pattern/structure of a slide "
        "must change. 'style' when the deck-wide palette / typography must change. "
        "'outline' when content structure or slide count must change.\n\n"
        "Return a JSON object: {ops: [ {target_stage, rationale, affected_slides, patch_fragment} ]}. "
        "patch_fragment is any partial state you want applied (e.g. "
        "{\"visual_style_preference\": \"use navy accent #14324c\"})."
    )
    user = (
        f"OUTLINE:\n{outline_md[:2000]}\n\n"
        f"STYLE:\n{visual_style_md[:1500]}\n\n"
        f"LAYOUTS:\n{layouts_summary}\n\n"
        f"COMMENTS (JSON):\n{comments}"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    result = zenmux.chat_structured(
        get_model("edit.intent"), messages, _EditOps, temperature=0.1
    )
    ops = [op.model_dump() for op in result.ops]
    return {"pending_edit_ops": ops}


def collapse_edit_ops(ops: list[dict[str, Any]]) -> tuple[str, dict[str, Any], list[int]]:
    """Earliest-stage-wins collapse.

    Returns (earliest_stage, merged_patch, union_of_affected_slides).
    If any op is html or image_only AND they share affected_slides, the slides list
    narrows downstream work.
    """
    if not ops:
        raise ValueError("no edit ops to collapse")
    earliest_idx = min(_STAGE_ORDER.index(op["target_stage"]) for op in ops)
    earliest = _STAGE_ORDER[earliest_idx]
    merged: dict[str, Any] = {}
    affected: list[int] = []
    for op in ops:
        merged.update(op.get("patch_fragment") or {})
        affected.extend(op.get("affected_slides") or [])
    return earliest, merged, sorted(set(affected))
