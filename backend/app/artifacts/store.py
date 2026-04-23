"""Derived artifact mirror on disk.

The LangGraph checkpointer is the authoritative state; this module writes a
human-inspectable mirror under ./threads/{thread_id}/. Never read back — if a
file goes missing, rehydrate from the checkpoint.

Layout:
  threads/{thread_id}/
    materials/{uuid}.{ext}
    outline.md
    visual_style.md
    layouts.md
    consolidated_brief.md
    slides/slide_{NN}.html
    comments.jsonl
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(os.getenv("OSZ_ARTIFACTS_DIR", "./threads"))


def thread_dir(thread_id: str) -> Path:
    d = ROOT / thread_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "slides").mkdir(exist_ok=True)
    (d / "materials").mkdir(exist_ok=True)
    return d


def write_markdown(thread_id: str, name: str, body: str) -> Path:
    d = thread_dir(thread_id)
    path = d / name
    path.write_text(body, encoding="utf-8")
    return path


def write_slide_html(thread_id: str, slide_idx: int, html: str) -> Path:
    d = thread_dir(thread_id) / "slides"
    path = d / f"slide_{slide_idx + 1:02d}.html"
    path.write_text(html, encoding="utf-8")
    return path


def invalidate_downstream(thread_id: str, from_stage: str) -> None:
    """Remove files that are downstream of the given stage (for regenerate)."""
    d = thread_dir(thread_id)
    order = ["outline.md", "visual_style.md", "layouts.md",
             "consolidated_brief.md"]
    stage_to_file = {
        "outline": "outline.md",
        "style": "visual_style.md",
        "layout": "layouts.md",
        "consolidate": "consolidated_brief.md",
    }
    if from_stage in stage_to_file:
        start = order.index(stage_to_file[from_stage])
        for name in order[start:]:
            p = d / name
            if p.exists():
                p.unlink()
    # Always clear HTML slides on regenerate.
    for html in (d / "slides").glob("*.html"):
        html.unlink()


def write_slide_mirrors(thread_id: str, state: dict[str, Any]) -> None:
    """Write all markdown + slide mirrors from a state snapshot."""
    if outline := state.get("outline_md"):
        write_markdown(thread_id, "outline.md", outline)
    if style_md := state.get("visual_style_md"):
        write_markdown(thread_id, "visual_style.md", style_md)
    if layouts := state.get("layouts"):
        body = ["# Layouts"]
        for l in layouts:
            body.append(f"## {l['slide_idx'] + 1}. {l.get('title')}")
            body.append(f"- pattern: `{l['pattern']}`  family: `{l['family']}`")
            body.append(f"- zones: {l['zones']}")
            body.append(f"- shape: {l.get('content_shape')}")
            if l.get("ranking_top3"):
                body.append("- ranking:")
                for r in l["ranking_top3"]:
                    body.append(f"  - {r['family']}: {r['score']}")
            if l.get("wireframe"):
                body.append("```")
                body.append(l["wireframe"])
                body.append("```")
            body.append("")
        write_markdown(thread_id, "layouts.md", "\n".join(body))
    if brief := state.get("consolidated_brief_md"):
        write_markdown(thread_id, "consolidated_brief.md", brief)
    if slides := state.get("html_slides"):
        for idx, html in slides.items():
            write_slide_html(thread_id, int(idx), html)


def append_comment(thread_id: str, comment: dict[str, Any]) -> None:
    d = thread_dir(thread_id)
    (d / "comments.jsonl").open("a", encoding="utf-8").write(
        json.dumps(comment, ensure_ascii=False) + "\n"
    )


