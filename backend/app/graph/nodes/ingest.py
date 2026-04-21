"""Stage B — parse uploaded materials into normalized text + metadata.

Text files read verbatim. PDF/docx parsing is left as a hook (not MVP-critical).
Images routed through a vision model to extract their content as text.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ...llm import zenmux
from ...llm.models import get_model

log = logging.getLogger(__name__)


def _parse_image(uri: str) -> str:
    model = get_model("ingest.vision")
    prompt = (
        "You are extracting slide-source material from an image. "
        "Produce a neutral, structured text description covering: headline claims, "
        "data points with units, tables (as Markdown), chart axes + notable values, "
        "and any visible captions. Keep the language of the original."
    )
    return zenmux.chat(
        model,
        [{"role": "user", "content": prompt}],
        images=[uri],
        temperature=0.1,
    )


def _parse_text(uri: str) -> str:
    """uri starts with 'text:' for inline, or is a local file path for .txt/.md."""
    if uri.startswith("text:"):
        return uri[len("text:"):]
    p = Path(uri)
    if not p.exists():
        raise FileNotFoundError(f"Material file not found: {uri}")
    if p.suffix.lower() in (".txt", ".md", ".csv"):
        return p.read_text(encoding="utf-8", errors="replace")
    # MVP: fall back to raw read for other files (user can pre-convert PDFs).
    return p.read_text(encoding="utf-8", errors="replace")


def ingest_node(state: dict[str, Any]) -> dict[str, Any]:
    materials = state.get("materials", [])
    out: list[dict[str, Any]] = []
    for m in materials:
        if m.get("parsed"):
            out.append(m)
            continue
        try:
            if m["kind"] == "image":
                parsed = _parse_image(m["uri"])
            else:
                parsed = _parse_text(m["uri"])
            out.append({**m, "parsed": parsed})
        except Exception as e:
            log.exception("Failed to parse material %s", m.get("uri"))
            out.append({**m, "parsed": "", "note": f"parse_error: {e}"})
    return {
        "materials": out,
        "current_stage": "outline",
    }
