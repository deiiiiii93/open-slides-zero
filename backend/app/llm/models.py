"""NODE → ZenMux model id mapping.

Every subagent reads its model id from here; override via env vars at process start.
Kept as a simple dict so callers can introspect and the HITL UI can show what's in use.
"""

from __future__ import annotations

import os

# Defaults chosen from the ZenMux model catalog:
#   - high-stakes reasoning / HTML composition → anthropic/claude-sonnet-4.6
#   - merge / classification / cheap tasks     → openai/gpt-5.4-mini
#   - vision / OCR                             → google/gemini-3.1-pro-preview
#   - image generation                         → sapiens-ai/agnes-image-1.2
_DEFAULTS: dict[str, str] = {
    "ingest.ocr":     "google/gemini-3.1-pro-preview",
    "outline":        "anthropic/claude-sonnet-4.6",
    "style.text":     "anthropic/claude-sonnet-4.6",
    "style.vision":   "google/gemini-3.1-pro-preview",
    "layout":         "anthropic/claude-sonnet-4.6",
    "consolidate":    "openai/gpt-5.4-mini",
    "html":           "anthropic/claude-sonnet-4.6",
    "edit.intent":    "openai/gpt-5.4-mini",
    "image_gen":      "sapiens-ai/agnes-image-1.2",
}


def _env_key(node: str) -> str:
    return "OSZ_MODEL_" + node.upper().replace(".", "_")


def get_model(node: str) -> str:
    override = os.getenv(_env_key(node))
    if override:
        return override
    if node not in _DEFAULTS:
        raise KeyError(f"No default model configured for node: {node}")
    return _DEFAULTS[node]


def all_models() -> dict[str, str]:
    return {node: get_model(node) for node in _DEFAULTS}


# Known vision-capable model prefixes — used by vision_capable() as a fast path.
# The ZenMux model catalog is the authoritative list; if a model isn't here, we
# fall through to a permissive default rather than blocking.
_VISION_PREFIXES = (
    "google/gemini",
    "openai/gpt-5",
    "openai/gpt-4",
    "anthropic/claude",
    "x-ai/grok",
    "xiaomi/mimo-v2-omni",
    "sapiens-ai/agnes-1.5-lite",
    "sapiens-ai/agnes-image",
    "qwen/qwen-image",
    "volcengine/doubao-seedream",
)


def vision_capable(model_id: str) -> bool:
    return any(model_id.startswith(p) for p in _VISION_PREFIXES)


# Preferred vision fallback when the routed model doesn't accept images.
VISION_FALLBACK = "google/gemini-3.1-pro-preview"


# Per-preset overlays for the html stage. Each entry may set a "model" id and/or
# a "temperature". A missing field falls back to the html default and 0.4 — so
# adding a preset here only changes the behaviors that are explicitly listed.
_HTML_PRESET_OVERLAYS: dict[str, dict[str, object]] = {
    "cartoon_fairytale_worlds":     {"temperature": 0.7},
    "design_portfolio_expression":  {"temperature": 0.6},
    "cultural_luxury":              {"temperature": 0.55},
    "product_clarity":              {"temperature": 0.4},
    "editorial_authority":          {"temperature": 0.3},
    "strategic_prestige":           {"temperature": 0.25},
}


def html_overlay_for_preset(preset_id: str | None) -> dict[str, object]:
    """Return the html-stage overlay for a preset id, or an empty dict."""
    if not preset_id:
        return {}
    return dict(_HTML_PRESET_OVERLAYS.get(preset_id, {}))
