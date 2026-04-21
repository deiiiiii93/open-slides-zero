"""Stage I — single-slide HTML composer.

Invoked via LangGraph `Send()` once per slide so that a failure on one slide
retries only that slide. Writes into `html_slides[slide_idx]` which is merged by
the `_merge_html` reducer in state.py.

The system prompt encodes the anti-slop rules verbatim.
"""

from __future__ import annotations

from typing import Any

from ...catalog import layouts as L
from ...catalog.validator import validate_slide_html
from ...llm import zenmux
from ...llm.models import get_model
from ...llm.stream import push_event, tagged_stream


ANTI_SLOP_RULES = """\
Avoid AI-slop tropes (these are non-negotiable):
- NO aggressive gradient backgrounds.
- NO emoji unless explicitly part of the brand; prefer text placeholders.
- NO containers using rounded corners with a left-border accent color.
- NO imagery drawn inline via SVG; use <img data-prompt-hint="..."> placeholders.
- NO overused font families (Inter, Roboto, Arial, Fraunces, system-ui, -apple-system).
- DO use `text-wrap: pretty`, CSS grid, modern advanced CSS (subgrid, container
  queries, :has()) where they actually help readability.
- Commit to a single bold aesthetic direction — do not hedge.
"""


def _canvas_css(aspect_ratio: str) -> tuple[int, int]:
    return L.CANVAS_SIZES.get(aspect_ratio, L.CANVAS_SIZES["16:9"])


def _slide_prompt(
    brief_slide: dict[str, Any],
    brief: dict[str, Any],
    feedback: str | None = None,
) -> list[dict[str, Any]]:
    width, height = _canvas_css(brief["aspect_ratio"])
    style = brief["style"]
    palette = style.get("palette", {})
    typography = style.get("typography", {})
    pattern = brief_slide["pattern"]
    zones = brief_slide["zones"]

    system = f"""\
You are an HTML/CSS slide composer. Output ONE complete self-contained HTML
document for a single slide. Hard constraints:

- Root <div> must be EXACTLY {width}px wide and {height}px tall,
  with overflow:hidden, box-sizing:border-box, position:relative.
- No pagination text ("Slide X of Y") or pagination dots.
- No overflow:auto or overflow:scroll anywhere.
- All <img> placeholders must have explicit width/height attributes AND
  a data-prompt-hint="..." attribute describing what image to generate later,
  AND object-fit:cover.
- Use inline <style> — no external stylesheet or JS frameworks.
- Use the `{pattern}` layout pattern with zones: {zones}.

{ANTI_SLOP_RULES}

Design system to apply:
- Palette: {palette}
- Typography: {typography}
- Density: {brief['density']}
- Tone: {style.get('tone', 'editorial')}

Language of text: {brief['language']}.
"""
    feedback_block = (
        f"\n\nUSER FEEDBACK (apply to this slide): {feedback}\n"
        if feedback else ""
    )
    user = f"""Slide content to render:

Title: {brief_slide['title']}
Role: {brief_slide['role']}
Bullets: {brief_slide['bullets']}
Speaker notes (do NOT render verbatim, only for your reasoning): {brief_slide.get('speaker_notes', '')}
ASCII layout wireframe for reference:
{brief_slide.get('wireframe', '')}{feedback_block}

Return ONLY the complete HTML document. Begin with <!DOCTYPE html>.
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def html_one_node(state: dict[str, Any]) -> dict[str, Any]:
    """Invoked via Send({'slide_idx': i, 'brief': brief}).

    LangGraph passes the Send payload as the state for this invocation.
    """
    brief = state.get("brief") or state.get("_brief")
    if not brief:
        return {"errors": [f"html_one: missing brief for slide {state.get('slide_idx')}"]}
    slide_idx = int(state["slide_idx"])

    slides = brief["slides"]
    slide = next((s for s in slides if s["slide_idx"] == slide_idx), None)
    if slide is None:
        return {"errors": [f"html_one: slide index {slide_idx} not in brief"]}

    feedback = state.get("feedback")
    messages = _slide_prompt(slide, brief, feedback=feedback)
    tag = f"html:{slide_idx}"
    push_event({"node": "html_one", "slide_idx": slide_idx, "state": "started"})
    with tagged_stream(tag):
        html = zenmux.chat(
            get_model("html"),
            messages,
            temperature=0.4,
            max_tokens=6000,
            stream=True,
        )
    push_event({"node": "html_one", "slide_idx": slide_idx, "state": "finished"})

    # Strip any markdown code fences the model may add despite instructions.
    html = html.strip()
    if html.startswith("```"):
        first_nl = html.find("\n")
        if first_nl != -1:
            html = html[first_nl + 1:]
        if html.endswith("```"):
            html = html[:-3]
    html = html.strip()

    validation = validate_slide_html(
        html,
        aspect_ratio=brief["aspect_ratio"],
        density=brief["density"],
    )

    update: dict[str, Any] = {"html_slides": {slide_idx: html}}
    if validation.errors:
        update["errors"] = [
            f"slide {slide_idx}: {e.rule} — {e.message}" for e in validation.errors
        ]
    return update
