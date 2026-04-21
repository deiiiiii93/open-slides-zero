"""Stage F — visual style designer.

Produces a design system spec: color palette (hex), typography (excluding banned
fonts), density class, imagery policy, tone (editorial/mbb/academic), slot image
preferences. Accepts an optional reference image (vision-routed when present).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ...llm import zenmux
from ...llm.models import get_model
from ...llm.stream import push_event, tagged_stream


BANNED_FONTS = ["Inter", "Roboto", "Arial", "Fraunces", "system-ui", "-apple-system"]


class _Typography(BaseModel):
    heading_family: str
    body_family: str
    display_family: str | None = None
    rationale: str = ""


class _Palette(BaseModel):
    primary: str                # hex
    secondary: str
    accent: str
    neutral_dark: str
    neutral_light: str
    background: str
    roles: dict[str, str] = Field(default_factory=dict)  # e.g. {"success": "#...", "warning": "#..."}


class _VisualStyle(BaseModel):
    tone: str = Field(description="editorial | mbb | academic | keynote | product")
    palette: _Palette
    typography: _Typography
    density: str = Field(description="minimal | balanced | dense | very_dense")
    imagery_policy: str
    motion_policy: str = "static"
    rationale: str = ""


def style_node(state: dict[str, Any]) -> dict[str, Any]:
    ref = state.get("style_reference_image_uri")
    user_pref = state.get("visual_style_preference") or ""
    outline_md = state.get("outline_md", "")

    system = (
        "You are a senior presentation visual designer. Produce a design system spec "
        "for a deck that will be rendered as HTML/CSS. Constraints:\n"
        f"- Do NOT select any of these overused fonts: {', '.join(BANNED_FONTS)}.\n"
        "- Choose editorially strong typefaces (e.g. serif display + humanist sans, "
        "slab headline + geometric body, or similar considered pairings).\n"
        "- All colors must be explicit hex values.\n"
        "- Imagery policy describes how images/placeholders are used (no SVG illustrations "
        "of people/things; always placeholders with prompt hints).\n"
        "- Density must be chosen from {minimal, balanced, dense, very_dense} "
        "consistent with Layout Catalog font limits."
    )

    user = (
        f"User preference hint: {user_pref or '(none)'}\n\n"
        f"Outline context (abridged):\n{outline_md[:6000]}"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    model = get_model("style.vision") if ref else get_model("style.text")
    push_event({"node": "style", "state": "started"})
    with tagged_stream("style"):
        result = zenmux.chat_structured(
            model,
            messages,
            _VisualStyle,
            images=[ref] if ref else None,
            temperature=0.4,
            stream=True,
        )
    push_event({"node": "style", "state": "finished"})

    pal = result.palette
    md = "\n".join([
        "# Visual Style",
        f"Tone: **{result.tone}**  |  Density: **{result.density}**",
        "",
        "## Palette",
        f"- primary: `{pal.primary}`",
        f"- secondary: `{pal.secondary}`",
        f"- accent: `{pal.accent}`",
        f"- neutral_dark: `{pal.neutral_dark}`",
        f"- neutral_light: `{pal.neutral_light}`",
        f"- background: `{pal.background}`",
        *[f"- {k}: `{v}`" for k, v in (pal.roles or {}).items()],
        "",
        "## Typography",
        f"- heading: {result.typography.heading_family}",
        f"- body: {result.typography.body_family}",
        f"- display: {result.typography.display_family or result.typography.heading_family}",
        f"_rationale: {result.typography.rationale}_",
        "",
        "## Imagery Policy",
        result.imagery_policy,
        "",
        "## Motion",
        result.motion_policy,
        "",
        "## Rationale",
        result.rationale,
    ])

    return {
        "visual_style_md": md,
        "visual_style": result.model_dump(),
        "current_stage": "layout",
    }
