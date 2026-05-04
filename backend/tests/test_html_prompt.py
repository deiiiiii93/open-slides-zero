"""HTML composer prompt tests."""

from app.catalog.visual_presets import VISUAL_STYLE_PRESETS
from app.graph.nodes.consolidate import consolidate_node
from app.graph.nodes.html_one import _slide_prompt


def test_html_prompt_includes_no_filler_rule():
    messages = _slide_prompt(
        {
            "slide_idx": 0,
            "title": "Title",
            "role": "opener",
            "bullets": ["Only real content"],
            "pattern": "title_body",
            "zones": ["title", "body"],
        },
        {
            "aspect_ratio": "16:9",
            "style": {
                "palette": {"background": "#fff", "text": "#111"},
                "typography": {"heading": "Aptos", "body": "Aptos"},
                "tone": "editorial",
            },
            "density": "balanced",
            "language": "en",
        },
    )

    system_prompt = messages[0]["content"]
    assert "Do not add filler content." in system_prompt
    assert "Every element should" in system_prompt
    assert "earn its place." in system_prompt
    assert "One thousand no's for every yes." in system_prompt
    assert 'data-image-placeholder="true"' in system_prompt
    assert "Add image here" in system_prompt
    assert "DO NOT emit <img>" in system_prompt
    assert "Use <img> only for real, non-empty src values" in system_prompt


def test_html_prompt_includes_visual_preference_guidance():
    preset = VISUAL_STYLE_PRESETS["product_clarity"]
    messages = _slide_prompt(
        {
            "slide_idx": 0,
            "title": "Title",
            "role": "opener",
            "bullets": ["Only real content"],
            "pattern": "title_body",
            "zones": ["title", "body"],
        },
        {
            "aspect_ratio": "16:9",
            "style": {
                "palette": {"background": "#fff", "text": "#111"},
                "typography": {"heading": "Aptos", "body": "Aptos"},
                "tone": "editorial",
            },
            "density": "balanced",
            "language": "en",
            "visual_style_preference": "Use calm enterprise restraint.",
            "visual_style_preset_label": preset["label"],
            "visual_style_preset_prompt": preset["prompt"],
            "visual_style_preset_html_rules": preset["html_rules"],
        },
    )

    system_prompt = messages[0]["content"]
    assert "Visual preference guidance" in system_prompt
    assert "Use calm enterprise restraint." in system_prompt
    assert "Selected preset (Product Clarity)" in system_prompt
    assert preset["prompt"] in system_prompt
    assert "Direction-specific HTML rules" in system_prompt
    assert preset["html_rules"][0] in system_prompt


def test_html_prompt_includes_composition_quality_rules():
    messages = _slide_prompt(
        {
            "slide_idx": 0,
            "title": "Title",
            "role": "opener",
            "bullets": ["Only real content"],
            "pattern": "title_body",
            "zones": ["title", "body"],
        },
        {
            "aspect_ratio": "16:9",
            "style": {
                "palette": {"background": "#fff", "text": "#111"},
                "typography": {"heading": "Aptos", "body": "Aptos"},
                "tone": "editorial",
            },
            "density": "balanced",
            "language": "en",
        },
    )

    system_prompt = messages[0]["content"]
    assert "Claude-quality composition discipline:" in system_prompt
    assert "Choose 2-4 named zones" in system_prompt
    assert "visible element must belong to one zone" in system_prompt
    assert "content-earned CSS-only motif" in system_prompt
    assert "thin rules, bands" in system_prompt
    assert "rings, simple bars" in system_prompt
    assert "at most two font-family roles" in system_prompt
    assert "cover/display 34-48px" in system_prompt
    assert "normal titles 28-40px" in system_prompt
    assert "body/proof 11.5-15px" in system_prompt
    assert "labels/meta 9-11px" in system_prompt
    assert "decorative numeral or glyph may exceed 64px" in system_prompt
    assert "Body/proof text must" in system_prompt
    assert "400, 600, and 700" in system_prompt
    assert "350, 450, 550, or 650" in system_prompt
    assert "title line-height 1.1-1.35" in system_prompt
    assert "body/proof line-height 1.45-1.7" in system_prompt
    assert "letter-spacing 0.08em-0.18em" in system_prompt
    assert "exact root canvas only" in system_prompt
    assert "@media rules" in system_prompt
    assert "responsive fallback CSS" in system_prompt
    assert "transitions, animations" in system_prompt
    assert "near-zero shadows" in system_prompt
    assert "external CSS imports" in system_prompt
    assert "external font imports" in system_prompt
    assert "hover states, animations" in system_prompt
    assert "title hierarchy first" in system_prompt

    user_prompt = messages[1]["content"]
    assert "Before returning the HTML, silently review" in user_prompt
    assert "exact canvas size" in user_prompt
    assert "role-based" in user_prompt
    assert "no external imports" in user_prompt
    assert "If any check fails, revise" in user_prompt


def test_html_prompt_omits_visual_direction_guidance_for_ai_decide():
    messages = _slide_prompt(
        {
            "slide_idx": 0,
            "title": "Title",
            "role": "opener",
            "bullets": ["Only real content"],
            "pattern": "title_body",
            "zones": ["title", "body"],
        },
        {
            "aspect_ratio": "16:9",
            "style": {
                "palette": {"background": "#fff", "text": "#111"},
                "typography": {"heading": "Aptos", "body": "Aptos"},
                "tone": "editorial",
            },
            "density": "balanced",
            "language": "en",
            "visual_style_preset_id": None,
            "visual_style_preset_label": None,
            "visual_style_preset_prompt": None,
            "visual_style_preset_html_rules": None,
        },
    )

    system_prompt = messages[0]["content"]
    assert "Visual preference guidance" not in system_prompt
    assert "Direction-specific HTML rules" not in system_prompt


def test_image_slots_survive_brief_and_reach_html_prompt():
    consolidated = consolidate_node({
        "language": "en",
        "aspect_ratio": "16:9",
        "visual_style": {
            "palette": {"background": "#fff", "text": "#111"},
            "typography": {"heading": "Aptos", "body": "Aptos"},
            "tone": "editorial",
            "density": "balanced",
        },
        "outline_slides": [
            {
                "title": "Three Views",
                "role": "context",
                "bullets": ["Morning, noon, and night views"],
                "image_slots": [
                    "morning exterior photo",
                    "noon interior detail",
                    "night skyline crop",
                ],
            }
        ],
        "layouts": [
            {
                "slide_idx": 0,
                "title": "Three Views",
                "pattern": "image_gallery_grid",
                "family": "grid",
                "zones": ["title", "image-1", "image-2", "image-3", "image-4", "caption"],
                "content_shape": "image_gallery",
                "wireframe": "",
            }
        ],
    })

    brief = consolidated["brief"]
    assert brief["slides"][0]["image_slots"] == [
        "morning exterior photo",
        "noon interior detail",
        "night skyline crop",
    ]
    assert "image_slots" in consolidated["consolidated_brief_md"]

    messages = _slide_prompt(brief["slides"][0], brief)
    joined = "\n\n".join(str(message["content"]) for message in messages)
    assert "Image slot guidance" in joined
    assert "render exactly one visible" in joined
    assert "morning exterior photo" in joined
    assert "data-prompt-hint" in joined
