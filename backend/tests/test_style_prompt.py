"""Visual style prompt tests."""

from app.graph.nodes import style
from app.catalog.visual_presets import visual_style_preset_state


def test_style_prompt_includes_color_usage_guidance(monkeypatch):
    captured: dict[str, object] = {}

    def fake_chat_structured(_model, messages, schema, **_kwargs):
        captured["messages"] = messages
        return schema(
            tone="editorial",
            density="balanced",
            palette={
                "primary": "#112233",
                "secondary": "#445566",
                "accent": "#AA5500",
                "neutral_dark": "#111111",
                "neutral_light": "#F5F5F5",
                "background": "#FFFFFF",
            },
            typography={
                "heading_family": "IBM Plex Serif",
                "body_family": "IBM Plex Sans",
                "display_family": "IBM Plex Serif",
                "rationale": "Strong contrast.",
            },
            imagery_policy="Use photographic placeholders.",
            motion_policy="static",
            rationale="Consistent with the story.",
        )

    monkeypatch.setattr(style.zenmux, "chat_structured", fake_chat_structured)

    style.style_node({"outline_md": "# Outline"})

    messages = captured["messages"]
    system_prompt = messages[0]["content"]
    assert "Color usage: try to use colors from brand / design system" in system_prompt
    assert "use oklch to define harmonious colors" in system_prompt
    assert "All colors must be explicit hex values." in system_prompt
    assert all("VISUAL DIRECTION PRESET" not in str(message["content"]) for message in messages)


def test_style_prompt_includes_visual_direction_style_bias(monkeypatch):
    captured: dict[str, object] = {}

    def fake_chat_structured(_model, messages, schema, **_kwargs):
        captured["messages"] = messages
        return schema(
            tone="editorial",
            density="balanced",
            palette={
                "primary": "#112233",
                "secondary": "#445566",
                "accent": "#AA5500",
                "neutral_dark": "#111111",
                "neutral_light": "#F5F5F5",
                "background": "#FFFFFF",
            },
            typography={
                "heading_family": "IBM Plex Serif",
                "body_family": "IBM Plex Sans",
                "display_family": "IBM Plex Serif",
                "rationale": "Strong contrast.",
            },
            imagery_policy="Use photographic placeholders.",
            motion_policy="static",
            rationale="Consistent with the story.",
        )

    monkeypatch.setattr(style.zenmux, "chat_structured", fake_chat_structured)

    state = {"outline_md": "# Outline"}
    state.update(visual_style_preset_state("cultural_luxury"))
    style.style_node(state)

    joined = "\n\n".join(str(message["content"]) for message in captured["messages"])
    assert "VISUAL DIRECTION PRESET" in joined
    assert "Cultural Luxury" in joined
    assert "premium editorial" in joined
    assert "large cinematic crops" in joined
