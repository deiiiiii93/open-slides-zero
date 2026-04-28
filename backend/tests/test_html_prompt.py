"""HTML composer prompt tests."""

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
