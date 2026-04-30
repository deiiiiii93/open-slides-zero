"""Deck export endpoints backed by browser rendering."""

from __future__ import annotations

import html as html_lib
import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .common import config_for, graph

router = APIRouter()

CANVAS: dict[str, tuple[int, int]] = {
    "16:9": (960, 540),
    "4:3": (960, 720),
    "21:9": (960, 411),
}
SCREENSHOT_WAIT_MS = 8000

CHROMIUM_EXECUTABLES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
PLAYWRIGHT_CHROMIUM_GLOBS = [
    "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "chromium_headless_shell-*/chrome-mac*/headless_shell",
    "chromium-*/chrome-linux/chrome",
    "chromium_headless_shell-*/chrome-linux/headless_shell",
]


class PngSlideIn(BaseModel):
    slide_idx: int = Field(ge=0)
    html: str = Field(min_length=1)


class PngExportIn(BaseModel):
    deck_name: str | None = None
    aspect_ratio: str = "16:9"
    slides: list[PngSlideIn] = Field(min_length=1)
    scale: int = Field(default=2, ge=1, le=3)
    base_url: str | None = None


class ThreadPngExportIn(BaseModel):
    deck_name: str | None = None
    aspect_ratio: str | None = None
    scale: int = Field(default=2, ge=1, le=3)
    base_url: str | None = None
    use_base_slides: bool = False


def _pad(n: int, width: int = 2) -> str:
    return str(n).zfill(width)


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip()
    return cleaned or "deck"


def _canvas_size(aspect_ratio: str) -> tuple[int, int]:
    return CANVAS.get(aspect_ratio, CANVAS["16:9"])


def _content_disposition(filename: str) -> str:
    safe = _sanitize_filename(filename)
    ascii_fallback = safe.encode("ascii", "replace").decode("ascii").replace("?", "_")
    quoted = ascii_fallback.replace("\\", "\\\\").replace('"', '\\"')
    encoded = quote(safe, safe="")
    return f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{encoded}"


def _base_href(base_url: str | None, request: Request) -> str:
    raw = (base_url or str(request.base_url)).strip()
    if not raw:
        raw = str(request.base_url)
    if not raw.endswith("/"):
        raw += "/"
    return raw


def _html_with_base(html: str, base_url: str) -> str:
    if re.search(r"<base\b", html, flags=re.IGNORECASE):
        return html
    base = f'<base href="{html_lib.escape(base_url, quote=True)}">'
    if re.search(r"<head\b[^>]*>", html, flags=re.IGNORECASE):
        return re.sub(
            r"(<head\b[^>]*>)",
            lambda match: f"{match.group(1)}\n{base}",
            html,
            count=1,
            flags=re.IGNORECASE,
        )
    return f"<!DOCTYPE html><html><head>{base}</head><body>{html}</body></html>"


def _existing_chromium_executable() -> str | None:
    candidates = [Path(path) for path in CHROMIUM_EXECUTABLES]
    cache_roots = [
        Path.home() / "Library/Caches/ms-playwright",
        Path.home() / ".cache/ms-playwright",
    ]
    for root in cache_roots:
        if not root.exists():
            continue
        for pattern in PLAYWRIGHT_CHROMIUM_GLOBS:
            candidates.extend(sorted(root.glob(pattern), reverse=True))

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return None


async def _launch_chromium(playwright: Any) -> Any:
    try:
        return await playwright.chromium.launch()
    except Exception:
        executable = _existing_chromium_executable()
        if not executable:
            raise
        return await playwright.chromium.launch(executable_path=executable)


def _png_index_html(slides: list[PngSlideIn], deck_name: str, canvas: tuple[int, int]) -> str:
    width, height = canvas
    escaped_name = html_lib.escape(deck_name)
    items = "\n".join(
        f"""    <figure>
      <img src="slide_{_pad(slide.slide_idx + 1)}.png" width="{width}" height="{height}" alt="Slide {slide.slide_idx + 1}" />
      <figcaption>Slide {slide.slide_idx + 1}</figcaption>
    </figure>"""
        for slide in slides
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{escaped_name} PNGs</title>
  <style>
    body {{ margin: 0; padding: 24px; background: #f5f5f5; font-family: Georgia, serif; color: #111; }}
    h1 {{ margin: 0 0 16px; }}
    figure {{ margin: 0 0 24px; }}
    img {{ display: block; width: {width}px; height: {height}px; border: 1px solid #ccc; background: #fff; }}
    figcaption {{ margin-top: 6px; font-size: 14px; color: #555; }}
  </style>
</head>
<body>
  <h1>{escaped_name} PNGs</h1>
{items}
</body>
</html>
"""


def _slides_from_values(values: dict[str, Any], *, use_base_slides: bool) -> list[PngSlideIn]:
    raw_slides = (
        values.get("html_slides_base")
        if use_base_slides and values.get("html_slides_base")
        else values.get("html_slides")
    )
    if not isinstance(raw_slides, dict):
        return []

    slides: list[PngSlideIn] = []
    for key, html in raw_slides.items():
        try:
            slide_idx = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(html, str) and html:
            slides.append(PngSlideIn(slide_idx=slide_idx, html=html))
    return sorted(slides, key=lambda slide: slide.slide_idx)


async def _wait_for_slide_assets(page: Any) -> None:
    await page.evaluate(
        """async (timeoutMs) => {
          const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
          const deadline = delay(timeoutMs);

          const dimensionToCss = (value) => /^\\d+(?:\\.\\d+)?$/.test(String(value).trim())
            ? `${String(value).trim()}px`
            : String(value).trim();
          for (const img of Array.from(document.querySelectorAll("img"))) {
            const rawSrc = img.getAttribute("src");
            if (rawSrc && rawSrc.trim()) continue;
            const slot = document.createElement("div");
            for (const attr of Array.from(img.attributes)) {
              if (attr.name.toLowerCase() !== "src") slot.setAttribute(attr.name, attr.value);
            }
            const hint = (
              slot.getAttribute("data-prompt-hint") ||
              img.getAttribute("alt") ||
              "image"
            ).trim();
            slot.setAttribute("data-image-placeholder", "true");
            slot.setAttribute("data-prompt-hint", hint);
            slot.setAttribute("role", "img");
            slot.setAttribute("aria-label", `Add image here. Suggested: ${hint}`);
            const width = img.getAttribute("width");
            const height = img.getAttribute("height");
            if (width && !slot.style.width) slot.style.width = dimensionToCss(width);
            if (height && !slot.style.height) slot.style.height = dimensionToCss(height);
            slot.style.display = "flex";
            slot.style.flexDirection = "column";
            slot.style.alignItems = "center";
            slot.style.justifyContent = "center";
            slot.style.gap = slot.style.gap || "8px";
            slot.style.padding = slot.style.padding || "18px";
            slot.style.boxSizing = "border-box";
            slot.style.overflow = "hidden";
            slot.style.textAlign = "center";
            slot.style.background = slot.style.background || "rgba(148, 163, 184, 0.12)";
            slot.style.border = slot.style.border || "1.5px dashed rgba(71, 85, 105, 0.48)";
            slot.style.color = slot.style.color || "rgba(30, 41, 59, 0.88)";
            slot.style.fontFamily = slot.style.fontFamily || "inherit";
            const label = document.createElement("span");
            label.textContent = "Add image here";
            label.style.fontSize = "16px";
            label.style.fontWeight = "700";
            label.style.lineHeight = "1.15";
            const suggestion = document.createElement("span");
            suggestion.textContent = `Suggested: ${hint}`;
            suggestion.style.fontSize = "12px";
            suggestion.style.lineHeight = "1.35";
            suggestion.style.opacity = "0.74";
            suggestion.style.maxWidth = "86%";
            suggestion.style.overflowWrap = "anywhere";
            slot.replaceChildren(label, suggestion);
            img.replaceWith(slot);
          }

          const images = Array.from(document.images || []);
          for (const img of images) {
            img.loading = "eager";
            img.decoding = "sync";
          }
          const imageLoads = Promise.all(images.map((img) => {
            if (img.complete) return true;
            return new Promise((resolve) => {
              img.addEventListener("load", () => resolve(true), { once: true });
              img.addEventListener("error", () => resolve(true), { once: true });
            });
          }));

          await Promise.race([document.fonts?.ready ?? Promise.resolve(), deadline]);
          await Promise.race([imageLoads, deadline]);
          await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        }""",
        SCREENSHOT_WAIT_MS,
    )


async def _render_pngs(body: PngExportIn, request: Request) -> list[tuple[PngSlideIn, bytes]]:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail='PNG screenshot export requires Playwright. Run `pip install -e ".[dev]"` in backend.',
        ) from exc

    width, height = _canvas_size(body.aspect_ratio)
    base_href = _base_href(body.base_url, request)
    rendered: list[tuple[PngSlideIn, bytes]] = []

    try:
        async with async_playwright() as playwright:
            browser = await _launch_chromium(playwright)
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=body.scale,
            )
            try:
                for slide in body.slides:
                    page = await context.new_page()
                    try:
                        await page.set_content(
                            _html_with_base(slide.html, base_href),
                            wait_until="domcontentloaded",
                            timeout=30000,
                        )
                        try:
                            await page.wait_for_load_state("networkidle", timeout=5000)
                        except PlaywrightTimeoutError:
                            pass
                        await _wait_for_slide_assets(page)
                        png = await page.screenshot(
                            type="png",
                            clip={"x": 0, "y": 0, "width": width, "height": height},
                            animations="disabled",
                            caret="hide",
                            omit_background=False,
                            scale="device",
                        )
                        rendered.append((slide, png))
                    finally:
                        await page.close()
            finally:
                await context.close()
                await browser.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"PNG screenshot export failed: {exc}") from exc

    return rendered


@router.post("/exports/pngs")
async def export_pngs(body: PngExportIn, request: Request) -> Response:
    deck_name = _sanitize_filename(body.deck_name or "deck")
    canvas = _canvas_size(body.aspect_ratio)
    rendered = await _render_pngs(body, request)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zip_out:
        for slide, png in rendered:
            zip_out.writestr(f"slide_{_pad(slide.slide_idx + 1)}.png", png)
        zip_out.writestr("index.html", _png_index_html(body.slides, deck_name, canvas))

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition(f"{deck_name}-pngs.zip")},
    )


@router.post("/decks/{thread_id}/exports/pngs")
async def export_thread_pngs(thread_id: str, body: ThreadPngExportIn, request: Request) -> Response:
    snap = graph().get_state(config_for(thread_id))
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown deck")

    values = dict(snap.values)
    slides = _slides_from_values(values, use_base_slides=body.use_base_slides)
    if not slides:
        raise HTTPException(status_code=400, detail="No rendered slides to export")

    deck_name = _sanitize_filename(
        body.deck_name
        or str(values.get("deck_name") or "")
        or f"deck-{thread_id[:6]}"
    )
    export_body = PngExportIn(
        deck_name=deck_name,
        aspect_ratio=body.aspect_ratio or str(values.get("aspect_ratio") or "16:9"),
        slides=slides,
        scale=body.scale,
        base_url=body.base_url,
    )
    rendered = await _render_pngs(export_body, request)
    canvas = _canvas_size(export_body.aspect_ratio)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zip_out:
        for slide, png in rendered:
            zip_out.writestr(f"slide_{_pad(slide.slide_idx + 1)}.png", png)
        zip_out.writestr("index.html", _png_index_html(slides, deck_name, canvas))

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition(f"{deck_name}-pngs.zip")},
    )
