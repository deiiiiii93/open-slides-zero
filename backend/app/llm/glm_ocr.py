"""Standalone client for Z.AI's `glm-ocr` layout_parsing endpoint.

Purpose: purpose-built document OCR that returns markdown in one call. Cheaper
and usually stronger on CJK documents than a general VLM prompted for OCR.

This is deliberately NOT routed through ZenMux — ZenMux aggregates chat
completions, not specialty OCR APIs. Config is env-only:

  OSZ_MODEL_OCR           e.g. "glm-ocr"
  OSZ_MODEL_OCR_BASE_URL  full endpoint URL, e.g.
                          "https://api.z.ai/api/paas/v4/layout_parsing"
  OSZ_MODEL_OCR_KEY       Z.AI API key (id.secret format)

API quirk: the `file` field accepts either an http(s) URL or a data URI —
BUT only for image mime types (image/png, image/jpeg). PDF-as-data-URI is
rejected with code 1214 despite what the docs claim. Callers must therefore
rasterize PDFs page-by-page before submitting.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

MODEL_ENV = "OSZ_MODEL_OCR"
BASE_URL_ENV = "OSZ_MODEL_OCR_BASE_URL"
KEY_ENV = "OSZ_MODEL_OCR_KEY"


class GLMOCRError(RuntimeError):
    """Non-retriable GLM-OCR failure (validation, auth, unsupported format)."""


class _RetriableError(RuntimeError):
    """Transient upstream failure — 429 / 5xx / network timeout."""


def is_configured() -> bool:
    return all(os.getenv(k) for k in (MODEL_ENV, BASE_URL_ENV, KEY_ENV))


def _config() -> tuple[str, str, str]:
    missing = [k for k in (MODEL_ENV, BASE_URL_ENV, KEY_ENV) if not os.getenv(k)]
    if missing:
        raise GLMOCRError(f"GLM-OCR is not configured; missing env: {', '.join(missing)}")
    return os.environ[MODEL_ENV], os.environ[BASE_URL_ENV], os.environ[KEY_ENV]


_retry = retry(
    retry=retry_if_exception_type(_RetriableError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    reraise=True,
)


@_retry
def _post_layout_parsing(
    file_value: str,
    *,
    timeout: float,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    model, base_url, key = _config()
    body: dict[str, Any] = {"model": model, "file": file_value}
    if extra:
        body.update(extra)
    try:
        response = httpx.post(
            base_url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise _RetriableError(str(exc)) from exc

    if response.status_code in (408, 429, 500, 502, 503, 504):
        raise _RetriableError(f"GLM-OCR {response.status_code}: {response.text[:200]}")
    if response.status_code >= 400:
        raise GLMOCRError(f"GLM-OCR {response.status_code}: {response.text[:300]}")
    return response.json()


def extract_markdown(
    file_input: str,
    *,
    timeout: float = 60.0,
    start_page: int | None = None,
    end_page: int | None = None,
) -> str:
    """Submit an image URL, PDF URL, or image data URI and return md_results.

    `file_input` may be:
      - an http(s)://... URL (for PDF or image) — the fastest path, one call
        processes the whole document
      - a data:image/png;base64,... or data:image/jpeg;base64,... URI — for
        local images or per-page PDF rasterizations

    Returns the stripped `md_results` string; raises GLMOCRError if the
    response lacks usable content.
    """
    extra: dict[str, Any] = {}
    if start_page is not None:
        extra["start_page_id"] = start_page
    if end_page is not None:
        extra["end_page_id"] = end_page
    data = _post_layout_parsing(file_input, timeout=timeout, extra=extra or None)
    md = (data.get("md_results") or "").strip()
    if not md:
        log.warning("GLM-OCR returned empty md_results (usage=%s)", data.get("usage"))
    return md
