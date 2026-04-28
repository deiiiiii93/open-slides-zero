"""Visual direction presets applied across style, layout, and HTML generation."""

from __future__ import annotations

from typing import Any


AI_DECIDE_PRESET_ID = "ai_decide"


VISUAL_STYLE_PRESETS: dict[str, dict[str, Any]] = {
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
        "style_bias": {
            "tone": "clear product narrative with pragmatic executive polish",
            "palette": "brand-led neutrals, high-clarity contrast, one functional accent",
            "typography": "precise sans-forward hierarchy with crisp display emphasis",
            "density": "balanced/dense, with generous grouping and obvious scan paths",
            "imagery": "product screenshots, product moments, clean feature close-ups, practical context",
        },
        "layout_bias": {
            "prefer": [
                "cover_left_title",
                "content_f_shape",
                "chart_left_bullets_right",
                "data_split_metric",
            ],
            "avoid": [
                "radial_compact",
                "paginated_document",
                "editorial_full_bleed_campaign",
            ],
        },
        "html_rules": [
            "make the main user/product benefit visible before secondary proof",
            "use clean sectioning, precise alignment, and restrained product-led accents",
            "prefer product or workflow image slots over abstract decoration",
            "avoid theatrical atmosphere or ornamental composition unless the slide content demands it",
        ],
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
        "style_bias": {
            "tone": "serious editorial analysis with publication-grade authority",
            "palette": "restrained neutrals, muted brand accents, low-noise backgrounds",
            "typography": "editorial serif hierarchy paired with a highly readable body face",
            "density": "dense but disciplined, optimized for rereading and source-like credibility",
            "imagery": "documentary photographs, annotated evidence, pull quotes, tables, metadata",
        },
        "layout_bias": {
            "prefer": [
                "editorial_thesis_panel",
                "editorial_reason_cards",
                "paginated_document",
                "safe_vertical_stack",
            ],
            "avoid": [
                "radial_compact",
                "editorial_full_bleed_campaign",
                "cover_full_bleed",
            ],
        },
        "html_rules": [
            "use strong typographic hierarchy, metadata-like labels, and repeat-readable structure",
            "make arguments feel sourced, rigorous, and calm",
            "prefer article-like pacing over poster-like drama",
            "avoid playful, campaign-like, or character-led treatments",
        ],
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
        "style_bias": {
            "tone": "executive strategy memo with analytical confidence",
            "palette": "brand-aware sober palette, clear data colors, restrained accent usage",
            "typography": "sharp report typography with compact headings and legible dense body text",
            "density": "dense/very_dense when needed, but always structured and boardroom-readable",
            "imagery": "evidence visuals, charts, market maps, operating-model diagrams, KPI panels",
        },
        "layout_bias": {
            "prefer": [
                "data_dashboard",
                "data_split_metric",
                "editorial_selection_shortlist",
                "chart_left_bullets_right",
            ],
            "avoid": [
                "cover_full_bleed",
                "radial_compact",
                "editorial_full_bleed_campaign",
            ],
        },
        "html_rules": [
            "lead with the business implication and make evidence immediately inspectable",
            "use sober data presentation, tight grids, and executive whitespace",
            "make KPI and conclusion slides feel boardroom-ready",
            "avoid decorative or character-led styling unless it directly supports the argument",
        ],
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
        "style_bias": {
            "tone": "premium editorial",
            "palette": "deep neutrals, one warm accent, low saturation",
            "typography": "high-contrast serif display + restrained sans",
            "density": "minimal/balanced",
            "imagery": "large cinematic crops, atmosphere-first",
        },
        "layout_bias": {
            "prefer": [
                "cover_full_bleed",
                "cover_split_image",
                "editorial_hero_split",
                "narrative_focus",
            ],
            "avoid": [
                "data_dashboard",
                "three_parallel_columns",
                "content_card_grid",
            ],
        },
        "html_rules": [
            "use dramatic contrast and premium pacing",
            "prefer sparse copy blocks and image-led composition",
            "avoid busy KPI grids unless required by the slide content",
            "make atmospheric identity feel primary and details feel curated",
        ],
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
        "style_bias": {
            "tone": "creative studio case-study with a visible design point of view",
            "palette": "brand-aware but more expressive, with bold contrast and deliberate accent fields",
            "typography": "confident display type, distinctive scale shifts, and carefully controlled body text",
            "density": "balanced, using composition and hierarchy instead of pure information packing",
            "imagery": "case-study visuals, process artifacts, modular image grids, crafted mockups",
        },
        "layout_bias": {
            "prefer": [
                "editorial_hero_split",
                "mixed_2x2_focus",
                "radial_compact",
                "editorial_inverted_impact",
            ],
            "avoid": [
                "paginated_document",
                "safe_vertical_stack",
                "text_top_chart_bottom",
            ],
        },
        "html_rules": [
            "make the composition itself demonstrate craft and point of view",
            "use bold type scale, expressive grids, and controlled asymmetry",
            "prefer crafted visual systems over plain report layouts",
            "avoid generic document stacks or purely conventional chart pages",
        ],
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
        "style_bias": {
            "tone": "warm character-led story world with commercial clarity",
            "palette": "playful brand-aware color, friendly contrast, soft supporting neutrals",
            "typography": "expressive display hierarchy paired with readable rounded or humanist body text",
            "density": "balanced, with breathing room for characters, scenes, and story beats",
            "imagery": "character-first scenes, poster moments, toy-like product worlds, family warmth",
        },
        "layout_bias": {
            "prefer": [
                "cover_split_image",
                "content_card_grid",
                "mixed_2x2_focus",
                "radial_compact",
                "narrative_focus",
            ],
            "avoid": [
                "paginated_document",
                "data_dashboard",
                "editorial_thesis_panel",
            ],
        },
        "html_rules": [
            "make character or story-world presence visible whenever the slide content supports it",
            "use warm, approachable forms and poster-like composition without losing readability",
            "prefer image slots for characters, family scenes, product worlds, or playful retail moments",
            "avoid sober document styling unless the slide is explicitly data-heavy",
        ],
    },
}


def normalize_visual_style_preset_id(preset_id: str | None) -> str | None:
    """Normalize empty / AI Decide selections to no preset."""
    if preset_id is None:
        return None
    preset_id = preset_id.strip()
    if not preset_id or preset_id == AI_DECIDE_PRESET_ID:
        return None
    return preset_id


def list_visual_style_presets() -> list[dict[str, Any]]:
    """Return presets in the UI display order."""
    return [dict(preset) for preset in VISUAL_STYLE_PRESETS.values()]


def resolve_visual_style_preset(preset_id: str | None) -> dict[str, Any] | None:
    """Resolve a preset id, returning None for an empty selection."""
    preset_id = normalize_visual_style_preset_id(preset_id)
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
            "visual_style_preset_style_bias": None,
            "visual_style_preset_layout_bias": None,
            "visual_style_preset_html_rules": None,
        }
    return {
        "visual_style_preset_id": preset["id"],
        "visual_style_preset_label": preset["label"],
        "visual_style_preset_prompt": preset["prompt"],
        "visual_style_preset_style_bias": dict(preset["style_bias"]),
        "visual_style_preset_layout_bias": dict(preset["layout_bias"]),
        "visual_style_preset_html_rules": list(preset["html_rules"]),
    }
