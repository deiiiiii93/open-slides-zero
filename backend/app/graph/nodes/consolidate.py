"""Stage H — merge outline + style + layouts into a single brief.

Deterministic merge: no LLM call needed. The brief is structured so that the
per-slide html_one fan-out can read just the fields it cares about without
re-parsing markdown.
"""

from __future__ import annotations

import json
from typing import Any


def consolidate_node(state: dict[str, Any]) -> dict[str, Any]:
    outline_slides = state.get("outline_slides") or []
    layouts = state.get("layouts") or []
    style = state.get("visual_style") or {}

    per_slide: list[dict[str, Any]] = []
    for layout in layouts:
        idx = layout["slide_idx"]
        outline = outline_slides[idx] if idx < len(outline_slides) else {}
        per_slide.append({
            "slide_idx": idx,
            "title": layout.get("title") or outline.get("title"),
            "role": outline.get("role"),
            "bullets": outline.get("bullets", []),
            "speaker_notes": outline.get("speaker_notes", ""),
            "pattern": layout["pattern"],
            "family": layout["family"],
            "zones": layout["zones"],
            "content_shape": layout.get("content_shape"),
            "wireframe": layout.get("wireframe", ""),
        })

    brief = {
        "language": state.get("language", "en"),
        "aspect_ratio": state.get("aspect_ratio", "16:9"),
        "density": style.get("density", state.get("density_preference", "balanced")),
        "style": style,
        "visual_style_preference": state.get("visual_style_preference") or "",
        "visual_style_preset_id": state.get("visual_style_preset_id"),
        "visual_style_preset_label": state.get("visual_style_preset_label"),
        "visual_style_preset_prompt": state.get("visual_style_preset_prompt"),
        "visual_style_preset_style_bias": state.get("visual_style_preset_style_bias"),
        "visual_style_preset_layout_bias": state.get("visual_style_preset_layout_bias"),
        "visual_style_preset_html_rules": state.get("visual_style_preset_html_rules"),
        "creator_prompt": state.get("creator_prompt") or "",
        "slides": per_slide,
    }

    md_parts = [
        "# Deck Brief",
        f"- Language: {brief['language']}",
        f"- Aspect: {brief['aspect_ratio']}",
        f"- Density: {brief['density']}",
        "",
    ]
    if brief["creator_prompt"]:
        md_parts.extend([
            "## Creator Prompt",
            str(brief["creator_prompt"]),
            "",
        ])
    guidance_parts = []
    if brief["visual_style_preference"]:
        guidance_parts.append(f"Free-text hint: {brief['visual_style_preference']}")
    if brief["visual_style_preset_label"] and brief["visual_style_preset_prompt"]:
        guidance_parts.append(
            f"{brief['visual_style_preset_label']}: {brief['visual_style_preset_prompt']}"
        )
    if brief["visual_style_preset_html_rules"]:
        guidance_parts.append(
            "HTML rules: "
            + "; ".join(str(rule) for rule in brief["visual_style_preset_html_rules"])
        )
    if guidance_parts:
        md_parts.extend([
            "## Visual Preference Guidance",
            *guidance_parts,
            "",
        ])
    md_parts.extend([
        "## Style",
        "```json",
        json.dumps(style, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Slides",
    ])
    for s in per_slide:
        md_parts.append(f"### {s['slide_idx'] + 1}. {s['title']}")
        md_parts.append(f"- role: {s['role']}")
        md_parts.append(f"- pattern: `{s['pattern']}` (family={s['family']})")
        md_parts.append(f"- zones: {s['zones']}")
        md_parts.append(f"- shape: {s['content_shape']}")
        if s["bullets"]:
            md_parts.append("- bullets:")
            for b in s["bullets"]:
                md_parts.append(f"  - {b}")
        if s.get("wireframe"):
            md_parts.append("```")
            md_parts.append(s["wireframe"])
            md_parts.append("```")
        md_parts.append("")

    return {
        "consolidated_brief_md": "\n".join(md_parts),
        "current_stage": "html",
        "brief": brief,   # structured copy read by the Send fan-out in graph.py
        "html_failures": [],
        "pending_html_retry_slides": [],
    }
