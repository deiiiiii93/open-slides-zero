"""Visual direction presets applied before HTML generation."""

from __future__ import annotations

from typing import Any


VISUAL_STYLE_PRESETS: dict[str, dict[str, str]] = {
    "product_clarity": {
        "id": "product_clarity",
        "label": "Product Clarity",
        "description": "Clean product-led layouts that make complex technology feel simple and trustworthy.",
        "prompt": (
            "Inspired by world-class technology and product companies. Use clean layouts, "
            "strong hierarchy, generous spacing, precise typography, polished "
            "micro-interactions, and a small number of confident calls to action. "
            "The design should make complex ideas feel simple, usable, trustworthy, "
            "and inevitable."
        ),
    },
    "editorial_authority": {
        "id": "editorial_authority",
        "label": "Editorial Authority",
        "description": "Serious publication-style hierarchy with credible, repeat-readable information density.",
        "prompt": (
            "Inspired by academic magazines, research journals, and serious business "
            "publications. Use structured information density, strong typographic "
            "hierarchy, clear metadata, disciplined grids, restrained visuals, and "
            "article-like pacing. The design should communicate credibility, "
            "seriousness, and repeat readability."
        ),
    },
    "strategic_prestige": {
        "id": "strategic_prestige",
        "label": "Strategic Prestige",
        "description": "Executive consulting style: analytical, restrained, evidence-led, and boardroom-ready.",
        "prompt": (
            "Inspired by top consulting and strategy firms. Use insight-led "
            "composition, report-like structure, confident whitespace, executive-level "
            "clarity, sober data presentation, and minimal decoration. The design "
            "should feel analytical, authoritative, evidence-based, and boardroom-ready."
        ),
    },
    "cultural_luxury": {
        "id": "cultural_luxury",
        "label": "Cultural Luxury",
        "description": "Cinematic premium styling that privileges taste, atmosphere, and identity.",
        "prompt": (
            "Inspired by fashion houses and premium cultural brands. Use cinematic "
            "imagery, dramatic pacing, refined typography, sparse UI, elegant contrast, "
            "and atmosphere over explanation. The design should create desire, taste, "
            "identity, and emotional prestige before delivering details."
        ),
    },
    "design_portfolio_expression": {
        "id": "design_portfolio_expression",
        "label": "Design-Portfolio Expression",
        "description": "Bold creative-studio composition that demonstrates craft and a strong point of view.",
        "prompt": (
            "Inspired by leading design studios and creative agencies. Use bold "
            "typography, visual case-study structure, expressive grids, image-led "
            "navigation, and distinctive composition. The design itself should "
            "demonstrate taste, craft, originality, and a clear point of view."
        ),
    },
    "cartoon_fairytale_worlds": {
        "id": "cartoon_fairytale_worlds",
        "label": "Cartoon / Fairytale Worlds",
        "description": "Warm character-led fantasy styling with playful color, rounded forms, and story-world detail.",
        "prompt": (
            "Inspired by animation studios, children's entertainment, and "
            "character-led fantasy brands. Use character-first composition, playful "
            "color, rounded shapes, poster-like scenes, soft motion cues, illustrated "
            "details, and discoverable story-world elements. The design should feel "
            "like entering a warm, memorable universe, prioritizing wonder, emotion, "
            "and narrative charm over minimalist efficiency."
        ),
    },
}


def list_visual_style_presets() -> list[dict[str, str]]:
    """Return presets in the UI display order."""
    return [dict(preset) for preset in VISUAL_STYLE_PRESETS.values()]


def resolve_visual_style_preset(preset_id: str | None) -> dict[str, str] | None:
    """Resolve a preset id, returning None for an empty selection."""
    if not preset_id:
        return None
    if preset_id not in VISUAL_STYLE_PRESETS:
        raise ValueError(f"Unknown visual style preset: {preset_id}")
    return VISUAL_STYLE_PRESETS[preset_id]


def visual_style_preset_state(preset_id: str | None) -> dict[str, Any]:
    """Build the SlideState patch for a selected visual preset."""
    preset = resolve_visual_style_preset(preset_id)
    if preset is None:
        return {
            "visual_style_preset_id": None,
            "visual_style_preset_label": None,
            "visual_style_preset_prompt": None,
        }
    return {
        "visual_style_preset_id": preset["id"],
        "visual_style_preset_label": preset["label"],
        "visual_style_preset_prompt": preset["prompt"],
    }
