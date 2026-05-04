"""Stage F — visual style designer.

Produces a design system spec: color palette (hex), typography (excluding banned
fonts), density class, imagery policy, tone (editorial/mbb/academic), slot image
preferences. Accepts an optional reference image (vision-routed when present).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ...llm import zenmux
from ...llm.models import get_lane_model, get_lane_thinking_effort
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


def _visual_direction_style_guidance(state: dict[str, Any]) -> str:
    label = state.get("visual_style_preset_label")
    style_bias = state.get("visual_style_preset_style_bias") or {}
    if not label or not style_bias:
        return ""
    lines = [
        "VISUAL DIRECTION PRESET:",
        f"{label}",
        "",
        "Style bias to express strongly while preserving recognizable brand anchors when present:",
    ]
    for key, value in style_bias.items():
        lines.append(f"- {key}: {value}")
    lines.extend([
        "",
        "The resulting visual_style object must visibly reflect this direction in tone, "
        "palette, typography, density, imagery policy, and rationale. Do not treat it "
        "as a surface-level HTML-only hint.",
    ])
    return "\n".join(lines)


def style_node(state: dict[str, Any]) -> dict[str, Any]:
    ref = state.get("style_reference_image_uri")
    user_pref = state.get("visual_style_preference") or ""
    creator_prompt = (state.get("creator_prompt") or "").strip()
    outline_md = state.get("outline_md", "")

    system = (
        "You are a senior presentation visual designer. Produce a design system spec "
        "for a deck that will be rendered as HTML/CSS. Constraints:\n"
        f"- Do NOT select any of these overused fonts: {', '.join(BANNED_FONTS)}.\n"
        "- Choose editorially strong typefaces (e.g. serif display + humanist sans, "
        "slab headline + geometric body, or similar considered pairings).\n"
        "- Color usage: try to use colors from brand / design system, if you have one. "
        "If it's too restrictive, use oklch to define harmonious colors that match "
        "the existing palette. Avoid inventing new colors from scratch.\n"
        "- All colors must be explicit hex values.\n"
        "- Imagery policy describes how images/image slots are used (no SVG illustrations "
        "of people/things; empty image positions must be visible "
        "data-image-placeholder=\"true\" slots with prompt hints, not empty <img> tags).\n"
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
    if visual_direction_guidance := _visual_direction_style_guidance(state):
        messages.append({"role": "user", "content": visual_direction_guidance})
    if creator_prompt:
        messages.append({
            "role": "user",
            "content": (
                "CREATOR PLAYGROUND LANE EXTRA INSTRUCTIONS:\n"
                f"{creator_prompt}\n\n"
                "Treat these as direct user instructions for this lane."
            ),
        })

    default_node = "style.vision" if ref else "style.text"
    model = get_lane_model(state, "style", default_node)
    reasoning_effort = get_lane_thinking_effort(state, "style")
    event: dict[str, Any] = {"node": "style", "state": "started", "model": model}
    if reasoning_effort:
        event["reasoning_effort"] = reasoning_effort
    push_event(event)
    with tagged_stream("style"):
        result = zenmux.chat_structured(
            model,
            messages,
            _VisualStyle,
            images=[ref] if ref else None,
            temperature=0.4,
            reasoning_effort=reasoning_effort,
            stream=True,
        )
    push_event({"node": "style", "state": "finished", "model": model})

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
