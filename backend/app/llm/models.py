"""NODE → ZenMux model id mapping.

Every subagent reads its model id from here; override via env vars at process start.
Kept as a simple dict so callers can introspect and the HITL UI can show what's in use.
"""

from __future__ import annotations

import os
from typing import Any

# Defaults chosen from the ZenMux model catalog:
#   - high-stakes reasoning / HTML composition → anthropic/claude-sonnet-4.6
#   - merge / classification / cheap tasks     → openai/gpt-5.4-mini
#   - vision / OCR                             → google/gemini-3.1-pro-preview
#   - image generation                         → openai/gpt-image-2
_DEFAULTS: dict[str, str] = {
    "ingest.ocr":     "google/gemini-3.1-pro-preview",
    "digest":         "openai/gpt-5.4-mini",
    "embeddings":     "openai/text-embedding-3-small",
    "advanced_chat":  "anthropic/claude-sonnet-4.6",
    "outline":        "anthropic/claude-sonnet-4.6",
    "style.text":     "anthropic/claude-sonnet-4.6",
    "style.vision":   "google/gemini-3.1-pro-preview",
    "layout":         "anthropic/claude-sonnet-4.6",
    "consolidate":    "openai/gpt-5.4-mini",
    "html":           "anthropic/claude-sonnet-4.6",
    "image_plan":     "openai/gpt-5.4-mini",
    "edit.intent":    "openai/gpt-5.4-mini",
    "image_gen":      "openai/gpt-image-2",
}

LANE_MODEL_STAGES = ("style", "layout", "html")
THINKING_EFFORT_VALUES = ("minimal", "low", "medium", "high")

_CURATED_LANE_MODEL_OPTIONS: list[dict[str, str]] = [
    {
        "id": "anthropic/claude-sonnet-4.6",
        "label": "Claude Sonnet 4.6",
        "description": "Strong default for creative slide reasoning and HTML composition.",
    },
    {
        "id": "google/gemini-3.1-pro-preview",
        "label": "Gemini 3.1 Pro Preview",
        "description": "Vision-capable option for lanes that need image-aware styling.",
    },
    {
        "id": "openai/gpt-5.4",
        "label": "GPT-5.4",
        "description": "General high-capability option for style, layout, and HTML stages.",
    },
    {
        "id": "openai/gpt-5.4-mini",
        "label": "GPT-5.4 Mini",
        "description": "Faster option for lighter lane experiments.",
    },
    {
        "id": "deepseek/deepseek-v4-flash",
        "label": "DeepSeek V4 Flash",
        "description": "Fast DeepSeek option for lower-latency style, layout, and HTML trials.",
    },
    {
        "id": "xiaomi/mimo-v2.5",
        "label": "MiMo V2.5",
        "description": "Xiaomi MiMo option for alternate reasoning and composition runs.",
    },
    {
        "id": "moonshotai/kimi-k2.6",
        "label": "Kimi K2.6",
        "description": "Moonshot Kimi option for alternate long-context slide generation.",
    },
]

_CURATED_THINKING_EFFORT_OPTIONS: list[dict[str, str]] = [
    {
        "id": "minimal",
        "label": "Minimal",
        "description": "Lowest thinking effort; useful for fast/lightweight trials.",
    },
    {
        "id": "low",
        "label": "Low",
        "description": "Reduced thinking effort for routine slide generation.",
    },
    {
        "id": "medium",
        "label": "Medium",
        "description": "Balanced thinking effort for normal deck work.",
    },
    {
        "id": "high",
        "label": "High",
        "description": "More thinking effort for difficult layout and composition work.",
    },
]


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


def lane_model_options() -> dict[str, Any]:
    """Return the curated model choices supported by Creator Playground lanes."""
    options = [dict(option) for option in _CURATED_LANE_MODEL_OPTIONS]
    effort_options = [dict(option) for option in _CURATED_THINKING_EFFORT_OPTIONS]
    return {
        "stages": {
            "style": {
                "label": "Style",
                "default_model": get_model("style.text"),
                "options": options,
            },
            "layout": {
                "label": "Layout",
                "default_model": get_model("layout"),
                "options": options,
            },
            "html": {
                "label": "HTML",
                "default_model": get_model("html"),
                "options": options,
            },
        },
        "thinking_efforts": {
            "default_effort": None,
            "options": effort_options,
        },
    }


def normalize_lane_model_overrides(overrides: Any) -> dict[str, str]:
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise ValueError("model_overrides must be an object.")
    valid_models = {option["id"] for option in _CURATED_LANE_MODEL_OPTIONS}
    normalized: dict[str, str] = {}
    for raw_stage, raw_model in overrides.items():
        stage = str(raw_stage)
        if stage not in LANE_MODEL_STAGES:
            allowed = ", ".join(LANE_MODEL_STAGES)
            raise ValueError(f"Unknown model override stage '{stage}'. Allowed stages: {allowed}.")
        model = str(raw_model).strip()
        if not model:
            continue
        if model not in valid_models:
            raise ValueError(f"Unknown lane model id '{model}'.")
        normalized[stage] = model
    return normalized


def normalize_lane_thinking_effort_overrides(overrides: Any) -> dict[str, str]:
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise ValueError("thinking_effort_overrides must be an object.")
    normalized: dict[str, str] = {}
    for raw_stage, raw_effort in overrides.items():
        stage = str(raw_stage)
        if stage not in LANE_MODEL_STAGES:
            allowed = ", ".join(LANE_MODEL_STAGES)
            raise ValueError(
                f"Unknown thinking effort override stage '{stage}'. Allowed stages: {allowed}."
            )
        if raw_effort is None:
            continue
        effort = str(raw_effort).strip().lower()
        if not effort:
            continue
        if effort not in THINKING_EFFORT_VALUES:
            allowed = ", ".join(THINKING_EFFORT_VALUES)
            raise ValueError(
                f"Unknown thinking effort '{effort}'. Allowed values: {allowed}."
            )
        normalized[stage] = effort
    return normalized


def get_lane_model(
    state: dict[str, Any],
    stage: str,
    default_node: str,
    *,
    fallback_model: str | None = None,
) -> str:
    """Resolve a model for a lane-aware stage.

    Precedence is lane override, caller-provided fallback, then global node
    routing. HTML passes the visual-preset overlay as the fallback.
    """
    overrides = state.get("lane_model_overrides") or {}
    if isinstance(overrides, dict):
        override = overrides.get(stage)
        if isinstance(override, str) and override.strip():
            return override.strip()
    if fallback_model:
        return fallback_model
    return get_model(default_node)


def get_lane_thinking_effort(state: dict[str, Any], stage: str) -> str | None:
    """Resolve the per-run thinking effort override for a lane-aware stage."""
    overrides = state.get("lane_thinking_effort_overrides") or {}
    if isinstance(overrides, dict):
        effort = overrides.get(stage)
        if isinstance(effort, str) and effort.strip():
            return effort.strip().lower()
    return None


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
