"""HTML composer prompt tests."""

from app.catalog.visual_presets import VISUAL_STYLE_PRESETS
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
