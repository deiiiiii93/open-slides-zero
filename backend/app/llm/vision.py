"""Helpers for building multimodal OpenAI-style message content arrays.

ZenMux forwards to upstream providers verbatim — mixing text + image_url parts is
standard across OpenAI/Anthropic/Gemini-compatible endpoints.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any


def _guess_mime(path: Path) -> str:
    guess, _ = mimetypes.guess_type(str(path))
    return guess or "image/png"


def image_part_from_path(path: str | Path, *, detail: str = "auto") -> dict[str, Any]:
    """Build an image_url part from a local file path (base64-inlined)."""
    p = Path(path)
    mime = _guess_mime(p)
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{data}", "detail": detail},
    }


def image_part_from_url(url: str, *, detail: str = "auto") -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": url, "detail": detail}}


def text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def user_message(text: str, images: list[str | Path] | None = None) -> dict[str, Any]:
    """Build a single user message with optional image parts."""
    if not images:
        return {"role": "user", "content": text}
    parts: list[dict[str, Any]] = [text_part(text)]
    for img in images:
        s = str(img)
        if s.startswith(("http://", "https://", "data:")):
            parts.append(image_part_from_url(s))
        else:
            parts.append(image_part_from_path(s))
    return {"role": "user", "content": parts}
