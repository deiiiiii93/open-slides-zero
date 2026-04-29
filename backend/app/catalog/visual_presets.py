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
            "tone": "confident product narrative anchored in real product surfaces",
            "palette": "bright neutral surface (off-white or warm grey), one saturated brand accent used sparingly, no second decorative color",
            "typography": "confident contemporary sans (e.g. Söhne, Untitled Sans, Inter Display family) with a clear weight ladder; no generic system sans",
            "density": "balanced, with at least one breath-zone per slide",
            "imagery": "product UI screenshots, real device mocks, clean feature close-ups; not abstract decoration",
        },
        "layout_bias": {
            "prefer": [
                "cover_left_title",
                "cover_split_image",
                "content_f_shape",
                "chart_left_bullets_right",
                "data_split_metric",
            ],
            "avoid": [
                "radial_compact",
                "paginated_document",
                "editorial_full_bleed_campaign",
                "image_gallery_grid",
            ],
        },
        "html_rules": [
            "make the main user/product benefit visible before secondary proof",
            "use one obvious primary call-to-action per slide; never two competing CTAs",
            "when content allows, allocate a substantial portion of the slide (≈35% or more) to a real product surface or screenshot slot",
            "keep the background bright; reserve the saturated brand accent for highlight type, the CTA, or one focal element",
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
            "tone": "publication-grade analysis with on-the-record byline authority",
            "palette": "paper-like off-white or pale cream background, deep ink (near-black) primary text, one restrained accent (oxblood, forest, or muted gold)",
            "typography": "serif heading mandatory in spirit (Tiempos, Source Serif, GT Sectra, Lora-family) at display scale; humanist sans body for readability",
            "density": "very_dense, article-like — comfortable rereading depth",
            "imagery": "captioned documentary photographs, pull quotes set in serif, tabular data with metadata labels, footnoted figures",
        },
        "layout_bias": {
            "prefer": [
                "editorial_thesis_panel",
                "editorial_reason_cards",
                "paginated_document",
                "safe_vertical_stack",
                "content_f_shape",
            ],
            "avoid": [
                "radial_compact",
                "editorial_full_bleed_campaign",
                "cover_full_bleed",
                "cover_split_image",
            ],
        },
        "html_rules": [
            "set headlines in serif at display scale; never use sans for headings",
            "every slide must show a metadata strip (date, author, source, or section number) when the content allows",
            "keep the background paper-like (off-white or warm cream); never use a dark background",
            "use strong typographic hierarchy and footnote-like detail; argue, do not decorate",
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
            "tone": "executive strategy memo, KPI-led, calm under quantitative pressure",
            "palette": "cool dark navy as primary text and structure, one cool blue mid-tone for data, restrained warm accent (gold or amber) used only on the conclusion",
            "typography": "condensed grotesk for headings (e.g. National 2 Condensed, Söhne Condensed) paired with sober body sans",
            "density": "very_dense, KPI-first composition; numbers read first",
            "imagery": "charts, market maps, operating-model diagrams, KPI tiles, two-by-two matrices; never decorative photography",
        },
        "layout_bias": {
            "prefer": [
                "data_dashboard",
                "data_split_metric",
                "editorial_selection_shortlist",
                "chart_left_bullets_right",
                "editorial_execution_grid",
            ],
            "avoid": [
                "cover_full_bleed",
                "radial_compact",
                "editorial_full_bleed_campaign",
                "cover_split_image",
            ],
        },
        "html_rules": [
            "lead with the number, then the implication",
            "use a tight 12-column grid; data slides should show at least 4 KPI tiles when data is present",
            "reserve any warm accent for the conclusion or recommendation only — body must stay cool/sober",
            "use sober data presentation, executive whitespace, and minimal decoration",
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
            "tone": "cinematic premium editorial — atmosphere before explanation",
            "palette": "default to black or deep ink background, single warm metallic accent (champagne, copper, or muted gold); no light backgrounds, no second decorative color",
            "typography": "high-contrast display serif (e.g. Canela, Tiempos Headline, GT Super) paired with restrained sans body",
            "density": "minimal — single focal idea per slide",
            "imagery": "one large cinematic crop per slide, atmosphere-first; no thumbnails or grids of small images",
        },
        "layout_bias": {
            "prefer": [
                "cover_full_bleed",
                "cover_split_image",
                "editorial_hero_split",
                "narrative_focus",
                "editorial_full_bleed_campaign",
            ],
            "avoid": [
                "data_dashboard",
                "three_parallel_columns",
                "content_card_grid",
                "image_gallery_grid",
                "editorial_execution_grid",
            ],
        },
        "html_rules": [
            "default to a dark background unless the slide is explicitly editorial-light",
            "one hero image per slide — never grids of small thumbnails",
            "set the headline in display serif at very large size; let it breathe",
            "limit copy: single focal idea, atmosphere over explanation",
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
