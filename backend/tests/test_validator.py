"""HTML constraint validator tests."""

from app.catalog.validator import validate_slide_html


GOOD_SLIDE = """<!DOCTYPE html>
<html><head><style>*{box-sizing:border-box}</style></head>
<body>
  <div style="width:960px;height:540px;overflow:hidden;position:relative;box-sizing:border-box">
    <h1 style="font-size:28px">Title</h1>
    <p style="font-size:13px;text-wrap:pretty">Body text.</p>
    <div
      data-image-placeholder="true"
      data-prompt-hint="hero image"
      style="width:400px;height:240px;display:flex;align-items:center;justify-content:center;border:1px dashed #888;background:#f5f5f5"
    >
      <span>Add image here</span>
      <span>Suggested: hero image</span>
    </div>
  </div>
</body></html>"""


def test_good_slide_passes():
    r = validate_slide_html(GOOD_SLIDE)
    assert r.ok, [e.message for e in r.errors]


def test_missing_canvas_width_errors():
    bad = GOOD_SLIDE.replace("width:960px", "width:100%")
    r = validate_slide_html(bad)
    assert not r.ok
    assert any(e.rule == "canvas_width" for e in r.errors)


def test_valid_slide_with_large_head_still_passes():
    padded = GOOD_SLIDE.replace(
        "<head>",
        "<head>" + ("<meta name=\"x\" content=\"padding\">" * 80),
    )
    r = validate_slide_html(padded)
    assert r.ok, [e.message for e in r.errors]


def test_overflow_scroll_blocked():
    bad = GOOD_SLIDE.replace(
        "overflow:hidden;position:relative",
        "overflow:hidden;overflow:scroll;position:relative",
    )
    r = validate_slide_html(bad)
    assert any(e.rule == "no_scroll" for e in r.errors)


def test_pagination_text_blocked():
    bad = GOOD_SLIDE.replace("Title", "Title — Slide 1 of 5")
    r = validate_slide_html(bad)
    assert any(e.rule == "no_pagination_text" for e in r.errors)


def test_missing_closing_tags_fail():
    bad = GOOD_SLIDE.replace("</body></html>", "")
    r = validate_slide_html(bad)
    assert any(e.rule == "missing_body_close" for e in r.errors)
    assert any(e.rule == "missing_html_close" for e in r.errors)


def test_unbalanced_css_braces_fail():
    bad = GOOD_SLIDE.replace("</style>", "")
    bad = bad.replace("box-sizing:border-box}", "box-sizing:border-box")
    bad += "</style></head></body></html>"
    r = validate_slide_html(bad)
    assert any(e.rule == "unbalanced_css_braces" for e in r.errors)


def test_banned_font_warning():
    bad = GOOD_SLIDE.replace(
        "*{box-sizing:border-box}",
        "*{box-sizing:border-box;font-family:Inter,sans-serif}",
    )
    r = validate_slide_html(bad)
    assert any(w.rule == "banned_font" for w in r.warnings)


def test_visible_filler_text_warns_without_blocking():
    bad = GOOD_SLIDE.replace("Body text.", "Lorem ipsum sample text.")
    r = validate_slide_html(bad)
    assert r.ok, [e.message for e in r.errors]
    assert any(w.rule == "filler_text" for w in r.warnings)


def test_image_prompt_hint_placeholder_text_does_not_warn():
    html = GOOD_SLIDE.replace(
        'data-prompt-hint="hero image"',
        'data-prompt-hint="placeholder text for generated hero image"',
    )
    r = validate_slide_html(html)
    assert r.ok, [e.message for e in r.errors]
    assert not any(w.rule == "filler_text" for w in r.warnings)


def test_empty_img_placeholder_errors():
    bad = GOOD_SLIDE.replace(
        """<div
      data-image-placeholder="true"
      data-prompt-hint="hero image"
      style="width:400px;height:240px;display:flex;align-items:center;justify-content:center;border:1px dashed #888;background:#f5f5f5"
    >
      <span>Add image here</span>
      <span>Suggested: hero image</span>
    </div>""",
        '<img width="400" height="240" data-prompt-hint="hero image" style="object-fit:cover" />',
    )
    r = validate_slide_html(bad)
    assert any(e.rule == "image_src_required" for e in r.errors)


def test_image_placeholder_requires_prompt_hint():
    bad = GOOD_SLIDE.replace('data-prompt-hint="hero image"', "")
    r = validate_slide_html(bad)
    assert any(e.rule == "image_placeholder_hint" for e in r.errors)


def test_image_placeholder_requires_inline_dimensions():
    bad = GOOD_SLIDE.replace("width:400px;height:240px;", "")
    r = validate_slide_html(bad)
    assert any(e.rule == "image_placeholder_dimensions" for e in r.errors)


def test_image_placeholder_requires_visible_label():
    bad = GOOD_SLIDE.replace("Add image here", "Visual")
    r = validate_slide_html(bad)
    assert any(e.rule == "image_placeholder_label" for e in r.errors)


def test_media_rule_warns():
    bad = GOOD_SLIDE.replace(
        "*{box-sizing:border-box}",
        "*{box-sizing:border-box} @media (max-width: 600px){h1{font-size:20px}}",
    )
    r = validate_slide_html(bad)
    assert any(w.rule == "responsive_css_in_canvas" for w in r.warnings)


def test_hover_state_warns():
    bad = GOOD_SLIDE.replace(
        "*{box-sizing:border-box}",
        "*{box-sizing:border-box} h1:hover{color:red}",
    )
    r = validate_slide_html(bad)
    assert any(w.rule == "non_static_chrome" for w in r.warnings)


def test_transition_warns():
    bad = GOOD_SLIDE.replace(
        "*{box-sizing:border-box}",
        "*{box-sizing:border-box;transition:all 0.2s}",
    )
    r = validate_slide_html(bad)
    assert any(w.rule == "non_static_chrome" for w in r.warnings)


def test_keyframes_warns():
    bad = GOOD_SLIDE.replace(
        "*{box-sizing:border-box}",
        "*{box-sizing:border-box} @keyframes pulse{from{opacity:0}to{opacity:1}}",
    )
    r = validate_slide_html(bad)
    assert any(w.rule == "non_static_chrome" for w in r.warnings)


def test_off_scale_font_weight_warns():
    bad = GOOD_SLIDE.replace(
        '<h1 style="font-size:28px">Title</h1>',
        '<h1 style="font-size:28px;font-weight:550">Title</h1>',
    )
    r = validate_slide_html(bad)
    assert any(w.rule == "font_weight_offscale" for w in r.warnings)


def test_canonical_font_weights_pass():
    ok = GOOD_SLIDE.replace(
        '<h1 style="font-size:28px">Title</h1>',
        '<h1 style="font-size:28px;font-weight:600">Title</h1>',
    )
    r = validate_slide_html(ok)
    assert r.ok
    assert not any(w.rule == "font_weight_offscale" for w in r.warnings)


def test_external_stylesheet_link_errors():
    bad = GOOD_SLIDE.replace(
        "<head>",
        '<head><link rel="stylesheet" href="https://example.com/style.css">',
    )
    r = validate_slide_html(bad)
    assert any(e.rule == "external_resource_link" for e in r.errors)


def test_google_fonts_link_errors():
    bad = GOOD_SLIDE.replace(
        "<head>",
        '<head><link href="https://fonts.googleapis.com/css?family=Inter">',
    )
    r = validate_slide_html(bad)
    assert any(e.rule == "external_resource_link" for e in r.errors)


def test_at_import_errors():
    bad = GOOD_SLIDE.replace(
        "*{box-sizing:border-box}",
        "@import url('https://fonts.googleapis.com/css?family=Inter'); *{box-sizing:border-box}",
    )
    r = validate_slide_html(bad)
    assert any(e.rule == "external_resource_link" for e in r.errors)
