"""Stage G — per-slide layout designer.

LLM proposes 3-5 pattern candidates for each slide + feature signals. The pure-Python
scorer (catalog.scorer) ranks them and picks a family; if the top LLM candidate
belongs to the chosen family it wins, otherwise we fall back to the family default.

ASCII wireframes are rendered deterministically from the pattern's zones so they
line up with the Layout Catalog preview diagrams.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ...catalog import layouts as L
from ...catalog.scorer import ScoreBreakdown, SlideSignal, pick_pattern
from ...llm import zenmux
from ...llm.models import get_model
from ...llm.stream import push_event, tagged_stream


class _PerSlideSignals(BaseModel):
    slide_idx: int
    content_type: str
    semantic_family: str
    content_shape: str
    item_count: int
    text_length: int
    story_role: str | None = None
    candidate_patterns: list[str] = Field(default_factory=list, max_length=5)
    notes: str = ""


class _BulkSignals(BaseModel):
    slides: list[_PerSlideSignals]


def _ascii_wireframe(pattern_id: str) -> str:
    """Deterministic lightweight ASCII preview from the pattern's zone list."""
    p = L.get_pattern(pattern_id)
    zones = p["zones"]
    family = p["family"]

    if family == "horizontal" and len(zones) == 2:
        return "\n".join([
            "┌─────────┬─────────┐",
            f"│ {zones[0][:7]:<7} │ {zones[1][:7]:<7} │",
            "└─────────┴─────────┘",
        ])
    if family == "vertical":
        lines = ["┌─────────────────┐"]
        for z in zones:
            lines.append(f"│ {z[:15]:<15} │")
            lines.append("├─────────────────┤")
        lines[-1] = "└─────────────────┘"
        return "\n".join(lines)
    if family == "grid":
        cells = zones[:4] + [""] * (4 - min(4, len(zones)))
        return "\n".join([
            "┌────────┬────────┐",
            f"│{cells[0][:8]:^8}│{cells[1][:8]:^8}│",
            "├────────┼────────┤",
            f"│{cells[2][:8]:^8}│{cells[3][:8]:^8}│",
            "└────────┴────────┘",
        ])
    if family == "radial":
        return "\n".join([
            "     ┌──────┐     ",
            " ▄──▶│ core │◀──▄ ",
            "     └──────┘     ",
            "   ring · ring    ",
        ])
    if family == "narrative":
        return "\n".join([
            "┌───────────────────┐",
            f"│     {zones[0][:9]:^9}     │",
            "│                   │",
            f"│     {zones[-1][:9]:^9}     │",
            "└───────────────────┘",
        ])
    # Fallback: flat list
    return " | ".join(z for z in zones)


def _signal_from_outline_slide(
    outline_slide: dict[str, Any],
) -> tuple[SlideSignal, bool, bool]:
    role = (outline_slide.get("role") or "").lower()
    is_cover = role == "cover"
    is_closing = role in ("closing", "close")
    bullets = outline_slide.get("bullets") or []
    text_len = sum(len(b) for b in bullets) + len(outline_slide.get("title", ""))
    return (
        SlideSignal(
            content_type="content",
            semantic_family="freeform",
            content_shape="freeform_text",
            item_count=len(bullets),
            text_length=text_len,
            is_cover=is_cover,
            is_closing=is_closing,
            story_role=role if role else None,
            editorial_enabled=True,
        ),
        is_cover,
        is_closing,
    )


def layout_node(state: dict[str, Any]) -> dict[str, Any]:
    outline_slides = state.get("outline_slides") or []
    if not outline_slides:
        return {"errors": ["layout_node: missing outline_slides"], "layouts": []}

    style = state.get("visual_style") or {}
    density = style.get("density", state.get("density_preference", "balanced"))
    aspect_ratio = state.get("aspect_ratio", "16:9")
    languages = state.get("languages") or [state.get("language", "en")]

    pattern_catalog = "\n".join(
        f"- {pid} (family={p['family']}, kind={p['kind']}, zones={p['zones']})"
        for pid, p in L.PATTERNS.items()
    )

    # One LLM call enriches all slides at once — cheaper and preserves cross-slide consistency.
    messages = [
        {
            "role": "system",
            "content": (
                "For each slide, infer content_type, semantic_family, content_shape, "
                "item_count, text_length, story_role, and propose 3-5 candidate layout "
                "patterns from the catalog below. Only use pattern ids that exist."
            ),
        },
        {
            "role": "user",
            "content": (
                f"PATTERNS:\n{pattern_catalog}\n\n"
                f"OUTLINE SLIDES (JSON):\n{outline_slides}"
            ),
        },
    ]
    push_event({"node": "layout", "state": "started"})
    with tagged_stream("layout"):
        enriched = zenmux.chat_structured(
            get_model("layout"), messages, _BulkSignals, temperature=0.2, stream=True
        )
    push_event({"node": "layout", "state": "finished"})
    signal_by_idx = {s.slide_idx: s for s in enriched.slides}

    out: list[dict[str, Any]] = []
    previous_family: str | None = None
    for i, osl in enumerate(outline_slides):
        sig, is_cover, is_closing = _signal_from_outline_slide(osl)
        enrich = signal_by_idx.get(i)
        candidates: list[str] = []
        if enrich:
            sig.content_type = enrich.content_type or sig.content_type
            sig.semantic_family = enrich.semantic_family or sig.semantic_family
            sig.content_shape = enrich.content_shape or sig.content_shape
            sig.item_count = max(sig.item_count, enrich.item_count)
            sig.text_length = max(sig.text_length, enrich.text_length)
            sig.story_role = enrich.story_role or sig.story_role
            candidates = [c for c in enrich.candidate_patterns if c in L.PATTERNS]
        sig.aspect_ratio = aspect_ratio
        sig.density_preference = density
        sig.languages = languages
        sig.previous_family = previous_family
        sig.is_cover = is_cover
        sig.is_closing = is_closing

        decision = pick_pattern(sig, llm_candidates=candidates)
        pattern_id = decision["pattern"]
        family = decision["family"]
        previous_family = family

        ranking: list[ScoreBreakdown] = decision["ranking"]
        out.append({
            "slide_idx": i,
            "title": osl.get("title", f"Slide {i + 1}"),
            "pattern": pattern_id,
            "family": family,
            "zones": L.get_pattern(pattern_id)["zones"],
            "wireframe": _ascii_wireframe(pattern_id),
            "content_shape": sig.content_shape,
            "story_role": sig.story_role,
            "ranking_top3": [
                {"family": r.family, "score": round(r.score, 3), "components": r.components}
                for r in ranking
            ],
            "notes": (enrich.notes if enrich else ""),
        })

    return {
        "layouts": out,
        "current_stage": "await_layout",
    }
