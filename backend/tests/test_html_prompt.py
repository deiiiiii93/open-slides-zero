"""HTML composer prompt tests."""

from app.catalog.visual_presets import VISUAL_STYLE_PRESETS
from app.graph.nodes.consolidate import consolidate_node
from app.graph.nodes.html_one import _critic_messages, _slide_prompt


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
    assert "Highend-quality composition discipline:" in system_prompt
    assert "Choose 2-4 named zones" in system_prompt
    assert "visible element must belong to one zone" in system_prompt
    assert "content-earned CSS-only motif" in system_prompt
    assert "thin rules, bands" in system_prompt
    assert "rings, simple bars" in system_prompt
    assert "at most three font-family roles" in system_prompt
    assert "Use Exactly the font families from typography" in system_prompt
    assert "cover/display 34-48px" in system_prompt
    assert "normal titles 28-40px" in system_prompt
    assert "body/proof 11.5-15px" in system_prompt
    assert "labels/meta 9-11px" in system_prompt
    assert "decorative numeral or glyph may exceed 64px" in system_prompt
    assert "Body/proof text must" in system_prompt
    assert "400, 600, and 700" in system_prompt
    assert "350, 450, 550, or 650" in system_prompt
    assert "Reserve proper line spacing" in system_prompt
    assert "exact root canvas only" in system_prompt
    assert "@media rules" in system_prompt
    assert "responsive fallback CSS" in system_prompt
    assert "transitions, animations" in system_prompt
    assert "near-zero shadows" in system_prompt
    assert "external CSS imports" in system_prompt
    assert "hover states, animations" in system_prompt
    assert "title hierarchy first" in system_prompt
    assert "Fit all text at final pixel size with no truncation, scrolling" in system_prompt

    assert "Before returning the HTML, silently review" in system_prompt
    assert "exact canvas size" in system_prompt
    assert "role-based" in system_prompt
    assert "no external imports" in system_prompt
    assert "If any check fails, revise" in system_prompt

    assert "zone contents, not motifs" in system_prompt
    assert "Avoid use boring Arabic numerals" in system_prompt
    assert "do not count toward the motif limit" in system_prompt
    assert "Before returning cover/closing HTML, verify:" in system_prompt
    assert "not merely centered title + bullets" in system_prompt
    assert "explicit named zones in CSS/DOM comments or class names" in system_prompt
    assert "one, and only one, earned decorative motif" in system_prompt
    assert "proof rows, quote/callout, evidence strip" in system_prompt
    assert "conclusion or action, not only a recap" in system_prompt

    assert "Dense fit discipline:" in system_prompt
    assert "Treat root overflow:hidden only as a safety net" in system_prompt
    assert "No inner zone may rely on" in system_prompt
    assert "clipping, truncation, scroll, line-clamp, ellipsis" in system_prompt
    assert "make a zone budget for the exact canvas" in system_prompt
    assert "assign each requested pattern zone a grid track or" in system_prompt
    assert "place every visible element into one non-overlapping" in system_prompt
    assert "Use CSS grid for macro layout and flex-column inside zones" in system_prompt
    assert "Use the lower end of the existing role-based type scale" in system_prompt
    assert "do not introduce a" in system_prompt
    assert "new type scale" in system_prompt
    assert "reserve display_family" in system_prompt
    assert "controlled typographic anchor" in system_prompt
    assert "Compress by reducing visual chrome" in system_prompt
    assert "Do not remove factual" in system_prompt
    assert "content or hide overflow" in system_prompt
    assert "every text-bearing block must have explicit" in system_prompt
    assert "fit the 960x540 canvas with readable hierarchy and no overlap" in system_prompt

    assert "Aesthetic breath discipline:" in system_prompt
    assert "fail quality even without overflow" in system_prompt
    assert "every zone is filled" in system_prompt
    assert "boxed, or visually equal-weight" in system_prompt
    assert "quiet field or breath zone" in system_prompt
    assert "Do not enlarge type just because space exists" in system_prompt
    assert "lower end of the" in system_prompt
    assert "existing type scale" in system_prompt
    assert "dense, execution, grid, and compact-panel slides" in system_prompt
    assert "Compact panel headings are structural labels" in system_prompt
    assert "not slide titles" in system_prompt
    assert "open columns, hairline dividers, bands, and whitespace" in system_prompt
    assert "full" in system_prompt
    assert "bordered card grids" in system_prompt
    assert "leave space instead of adding chrome or scaling text" in system_prompt
    assert "Reduce chrome before increasing type size" in system_prompt


def test_html_prompt_includes_cover_and_closing_role_guidance():
    messages = _slide_prompt(
        {
            "slide_idx": 0,
            "title": "A Designed Opening",
            "role": "cover",
            "bullets": ["Proof point", "Second proof point"],
            "pattern": "cover_left_title",
            "zones": ["title", "subtitle", "visual"],
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
    assert "Use the `cover_left_title` layout pattern" in system_prompt
    assert "Role-specific composition guidance:" in system_prompt
    assert system_prompt.index("Use the `cover_left_title` layout pattern") < system_prompt.index(
        "Role-specific composition guidance:"
    )

    assert 'If Role is "cover":' in system_prompt
    assert "designed opening moment" in system_prompt
    assert "eyebrow/meta, display title, proof/support" in system_prompt
    assert "compact" in system_prompt
    assert "2-column evidence strip" in system_prompt
    assert "exactly one content-earned visual motif" in system_prompt
    assert "theme, chapter, source, date, or keynote" in system_prompt
    assert "Highlight 1-2 key words" in system_prompt

    assert 'If Role is "closing" or "close":' in system_prompt
    assert "synthesis plus next action" in system_prompt
    assert "closing label, final thesis, recap/proof" in system_prompt
    assert "Reuse the deck's established motif" in system_prompt
    assert "recap proof rows, decision columns, quote block" in system_prompt
    assert "one clear closing action, quote, or final line" in system_prompt
    assert "footer/contact/meta treatment" in system_prompt


def test_html_prompt_includes_binding_typography_role_contract():
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
                "typography": {
                    "heading_family": "Heading Serif",
                    "body_family": "Body Sans",
                    "display_family": "Display Face",
                },
                "tone": "editorial",
            },
            "density": "balanced",
            "language": "en",
        },
    )

    system_prompt = messages[0]["content"]
    assert "Typography role contract:" in system_prompt
    assert "body_family: `Body Sans`" in system_prompt
    assert "heading_family: `Heading Serif`" in system_prompt
    assert "display_family: `Display Face`" in system_prompt
    assert "not optional when present" in system_prompt
    assert "Use this exact CSS stack at least once" in system_prompt
    assert "controlled anchor only" in system_prompt


def test_html_critic_receives_typography_role_contract():
    messages = _critic_messages(
        brief_slide={
            "slide_idx": 0,
            "title": "Title",
            "role": "opener",
            "bullets": ["Only real content"],
            "pattern": "title_body",
            "zones": ["title", "body"],
        },
        brief={
            "style": {
                "typography": {
                    "heading_family": "Heading Serif",
                    "body_family": "Body Sans",
                    "display_family": "Display Face",
                },
                "tone": "editorial",
            },
            "density": "balanced",
        },
        html="<!DOCTYPE html><html><body><div class='slide'>Title</div></body></html>",
        warnings=[],
    )

    system_prompt = messages[0]["content"]
    user_prompt = messages[1]["content"]
    assert "Treat the typography role contract as binding" in system_prompt
    assert "display_family is present" in system_prompt
    assert "but unused" in system_prompt
    assert "cramped but technically fitting composition" in system_prompt
    assert "Aesthetic breath discipline:" in system_prompt
    assert "quiet field or breath zone" in system_prompt
    assert "Typography role contract:" in user_prompt
    assert "display_family: `Display Face`" in user_prompt
    assert "Use this exact CSS stack at least once" in user_prompt


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
