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
        "slides": per_slide,
    }

    md_parts = [
        "# Deck Brief",
        f"- Language: {brief['language']}",
        f"- Aspect: {brief['aspect_ratio']}",
        f"- Density: {brief['density']}",
        "",
        "## Style",
        "```json",
        json.dumps(style, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Slides",
    ]
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
    }
