"""Server-side font conversion. Browsers can't natively decompress woff2 to a
format installable by macOS Font Book / Windows; fontTools can. The frontend
POSTs woff2 bytes (which it already has, possibly via browser cache) and gets
TTF back — no upstream fetch, no SSRF surface, no cache-miss vs. browser-cache
mismatch."""

from __future__ import annotations

import asyncio
from io import BytesIO

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

router = APIRouter()

_MAX_FONT_BYTES = 5 * 1024 * 1024


def _woff2_to_ttf(blob: bytes) -> bytes:
    # Imported lazily so the rest of the app boots even if fontTools[woff] is missing.
    from fontTools.ttLib import TTFont

    font = TTFont(BytesIO(blob))
    out = BytesIO()
    font.flavor = None
    font.save(out)
    return out.getvalue()


@router.post("/font-as-ttf")
async def font_as_ttf(request: Request) -> Response:
    blob = await request.body()
    if not blob:
        raise HTTPException(400, "empty body")
    if len(blob) > _MAX_FONT_BYTES:
        raise HTTPException(413, f"font too large ({len(blob)} bytes)")
    if blob[:4] != b"wOF2":
        raise HTTPException(415, "not a WOFF2 file (bad magic)")

    try:
        ttf_bytes = await asyncio.to_thread(_woff2_to_ttf, blob)
    except Exception as exc:
        raise HTTPException(422, f"woff2 decompress failed: {exc}") from exc

    return Response(ttf_bytes, media_type="font/ttf")
