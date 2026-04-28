from app.api import decks
from app.catalog.visual_presets import (
    resolve_visual_style_preset,
    visual_style_preset_state,
)
from app.graph.nodes import layout


def test_catalog_exposes_visual_style_presets():
    catalog = decks.get_catalog("any-thread")
    presets = catalog["visual_style_presets"]

    assert [preset["id"] for preset in presets] == [
        "product_clarity",
        "editorial_authority",
        "strategic_prestige",
        "cultural_luxury",
        "design_portfolio_expression",
        "cartoon_fairytale_worlds",
    ]
    assert [preset["label"] for preset in presets] == [
        "Product Clarity",
        "Editorial Authority",
        "Strategic Prestige",
        "Cultural Luxury",
        "Design-Portfolio Expression",
        "Cartoon / Fairytale Worlds",
    ]
    assert all(preset["description"] for preset in presets)
    assert all(preset["style_bias"] for preset in presets)
    assert all(preset["layout_bias"] for preset in presets)
    assert all(preset["html_rules"] for preset in presets)


def test_ai_decide_resolves_to_no_visual_preset():
    assert resolve_visual_style_preset(None) is None
    assert resolve_visual_style_preset("") is None
    assert resolve_visual_style_preset("ai_decide") is None
    assert visual_style_preset_state("ai_decide") == {
        "visual_style_preset_id": None,
        "visual_style_preset_label": None,
        "visual_style_preset_prompt": None,
        "visual_style_preset_style_bias": None,
        "visual_style_preset_layout_bias": None,
        "visual_style_preset_html_rules": None,
    }


def test_layout_prompt_includes_bias_and_preferred_pattern_can_replace_default(monkeypatch):
    captured: dict[str, object] = {}

    def fake_chat_structured(_model, messages, schema, **_kwargs):
        captured["messages"] = messages
        return schema(
            slides=[
                {
                    "slide_idx": 0,
                    "content_type": "content",
                    "semantic_family": "freeform",
                    "content_shape": "freeform_text",
                    "item_count": 2,
                    "text_length": 20,
                    "story_role": "context",
                    "candidate_patterns": [],
                    "notes": "",
                }
            ]
        )

    monkeypatch.setattr(layout.zenmux, "chat_structured", fake_chat_structured)
    state = {
        "outline_slides": [
            {
                "title": "Product proof",
                "role": "context",
                "bullets": ["Benefit", "Evidence"],
            }
        ],
        "visual_style": {"density": "balanced"},
        "language": "en",
    }
    state.update(visual_style_preset_state("product_clarity"))

    result = layout.layout_node(state)

    joined = "\n\n".join(str(message["content"]) for message in captured["messages"])
    assert "VISUAL DIRECTION LAYOUT BIAS" in joined
    assert "content_f_shape" in joined
    assert "paginated_document" in joined
    assert result["layouts"][0]["family"] == "horizontal"
    assert result["layouts"][0]["pattern"] == "content_f_shape"
