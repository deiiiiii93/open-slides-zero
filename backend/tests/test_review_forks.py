from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import decks, hitl, history
from app.artifacts import store
from app.catalog.visual_presets import VISUAL_STYLE_PRESETS
from app.graph import graph as graph_module
from app.graph.nodes import html_one, layout, outline, style
from app.llm.zenmux import CompletionResult


def _interrupt_gate(state: dict) -> str | None:
    interrupts = state.get("interrupts") or []
    if not interrupts:
        return None
    payload = interrupts[0]
    return payload.get("gate")


@pytest.fixture()
def isolated_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    graph_module._compiled = None
    monkeypatch.setattr(graph_module, "DB_PATH", tmp_path / "threads.sqlite")
    monkeypatch.setattr(store, "ROOT", tmp_path / "threads")
    calls: dict[str, list[list[dict[str, object]]]] = {"html_messages": []}

    def fake_chat_structured(_model, _messages, schema, **_kwargs):
        if schema is outline._ProposedChoices:
            return schema(
                recommended_scenario_id="sales_pitch",
                candidate_structure_ids=["pyramid", "bluf"],
                rationale="fit",
            )
        if schema is outline._Outline:
            return schema(
                language="en",
                summary="demo",
                slides=[
                    {
                        "title": "Opening argument",
                        "role": "context",
                        "bullets": ["Top-line message", "Commercial proof"],
                    },
                    {
                        "title": "Execution path",
                        "role": "execution",
                        "bullets": ["Workstream one", "Workstream two"],
                    },
                ],
            )
        if schema is style._VisualStyle:
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
        if schema is layout._BulkSignals:
            return schema(
                slides=[
                    {
                        "slide_idx": 0,
                        "content_type": "content",
                        "semantic_family": "comparison",
                        "content_shape": "cards",
                        "item_count": 2,
                        "text_length": 60,
                        "story_role": "context",
                        "candidate_patterns": ["content_card_grid"],
                        "notes": "Grid works.",
                    },
                    {
                        "slide_idx": 1,
                        "content_type": "content",
                        "semantic_family": "narrative",
                        "content_shape": "timeline",
                        "item_count": 2,
                        "text_length": 55,
                        "story_role": "execution",
                        "candidate_patterns": ["narrative_focus"],
                        "notes": "Narrative focus works.",
                    },
                ],
            )
        if schema is html_one._HtmlCritique:
            return schema(decision="accept", issues=[], revision_instructions="")
        raise AssertionError(f"Unexpected schema: {schema}")

    def fake_chat(_model, messages, **_kwargs):
        calls["html_messages"].append(messages)
        prompt = messages[-1]["content"]
        title_line = next((line for line in prompt.splitlines() if line.startswith("Title: ")), "Title: Slide")
        pattern_line = next((line for line in messages[0]["content"].splitlines() if "Use the `" in line), "")
        title = title_line.removeprefix("Title: ")
        pattern = pattern_line.split("`")[1] if "`" in pattern_line else "unknown"
        return CompletionResult(
            text=(
                "<!DOCTYPE html><html><head><style>"
                "*{box-sizing:border-box}.slide{width:960px;height:540px;overflow:hidden;position:relative;}"
                "</style></head><body>"
                f"<div class=\"slide\"><h1 data-pattern=\"{pattern}\">{title}</h1></div>"
                "</body></html>"
            ),
            finish_reason="stop",
        )

    monkeypatch.setattr(outline.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(style.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(layout.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(html_one.zenmux, "chat_with_metadata", fake_chat)

    yield calls
    graph_module._compiled = None


def _create_ready_deck() -> dict:
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Forkable deck",
            expected_pages=2,
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    thread_id = created["thread_id"]
    structured = hitl.resume_deck(
        thread_id,
        {
            "scenario_id": created["values"]["scenario_id"],
            "structure_id": created["values"]["structure_candidates"][0],
        },
    )
    assert _interrupt_gate(structured) == "outline"
    styled = hitl.resume_deck(thread_id, {"approved": True})
    assert _interrupt_gate(styled) == "style"
    laid_out = hitl.resume_deck(thread_id, {"approved": True})
    assert _interrupt_gate(laid_out) == "layout"
    ready = hitl.resume_deck(thread_id, {"approved": True, "overrides": {}})
    assert ready["values"]["current_stage"] == "ready"
    return ready


def _inject_stale_generated_image_state(thread_id: str) -> dict:
    graph_module.get_graph().update_state(  # type: ignore[arg-type]
        history.config_for(thread_id),
        {
            "image_assets": [
                {
                    "asset_id": "user-asset",
                    "uri": "https://example.com/user.png",
                    "name": "user.png",
                    "source": "user",
                },
                {
                    "asset_id": "generated-slide-0",
                    "uri": "/tmp/generated-slide-0.png",
                    "name": "generated-slide-0.png",
                    "source": "generated",
                },
            ],
            "image_insertion_plan": {
                "status": "applied",
                "slots": [{"slot_id": "slide-0-slot-0"}],
                "mappings": [{"slot_id": "slide-0-slot-0", "asset_id": "generated-slide-0"}],
                "applied_mappings": [{"slot_id": "slide-0-slot-0", "asset_id": "generated-slide-0"}],
                "unmatched_slots": [],
            },
            "image_insertion_status": "applied",
            "image_generation_errors": [{"slide_idx": 0, "error": "old failure"}],
            "html_slides_base": {0: "<html><body>base</body></html>"},
        },
        as_node="post_html",
    )
    return decks.get_deck(thread_id)


def test_structure_review_fork_creates_new_thread_and_preserves_source(isolated_graph):
    source = _create_ready_deck()
    source_thread_id = source["thread_id"]

    forked = history.fork_from_review(
        source_thread_id,
        history.ForkFromStructureBody(
            review_stage="structure",
            scenario_id="sales_pitch",
            structure_id="bluf",
        ),
    )

    assert forked["thread_id"] != source_thread_id
    assert forked["source_thread_id"] == source_thread_id
    assert forked["values"]["deck_name"] == "Forkable deck (fork)"
    assert _interrupt_gate(forked) == "style"
    assert forked["values"]["structure_id"] == "bluf"

    current_source = decks.get_deck(source_thread_id)
    assert current_source["thread_id"] == source_thread_id
    assert current_source["values"]["current_stage"] == "ready"
    assert current_source.get("source_thread_id") is None


def test_style_review_fork_lands_at_style_interrupt_with_new_preference(isolated_graph):
    source = _create_ready_deck()

    forked = history.fork_from_review(
        source["thread_id"],
        history.ForkFromStyleBody(
            review_stage="style",
            feedback="Use a warmer palette and sharper serif contrast.",
        ),
    )

    assert _interrupt_gate(forked) == "style"
    assert forked["values"]["visual_style_preference"] == "Use a warmer palette and sharper serif contrast."


def test_layout_approval_stores_visual_preset_and_sends_it_to_html(isolated_graph):
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Preset deck",
            expected_pages=2,
            visual_style_preference="Use calm enterprise restraint.",
            visual_style_preset_id="product_clarity",
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    thread_id = created["thread_id"]
    structured = hitl.resume_deck(
        thread_id,
        {
            "scenario_id": created["values"]["scenario_id"],
            "structure_id": created["values"]["structure_candidates"][0],
        },
    )
    assert _interrupt_gate(structured) == "outline"
    styled = hitl.resume_deck(thread_id, {"approved": True})
    assert _interrupt_gate(styled) == "style"
    laid_out = hitl.resume_deck(thread_id, {"approved": True})
    assert _interrupt_gate(laid_out) == "layout"

    ready = hitl.resume_deck(
        thread_id,
        {
            "approved": True,
            "overrides": {},
            "visual_style_preset_id": "product_clarity",
        },
    )

    preset = VISUAL_STYLE_PRESETS["product_clarity"]
    assert ready["values"]["current_stage"] == "ready"
    assert ready["values"]["visual_style_preset_id"] == "product_clarity"
    assert ready["values"]["visual_style_preset_label"] == "Product Clarity"
    assert ready["values"]["brief"]["visual_style_preset_prompt"] == preset["prompt"]

    html_system_prompts = [messages[0]["content"] for messages in isolated_graph["html_messages"]]
    assert html_system_prompts
    assert all("Visual preference guidance" in prompt for prompt in html_system_prompts)
    assert all("Use calm enterprise restraint." in prompt for prompt in html_system_prompts)
    assert all(preset["prompt"] in prompt for prompt in html_system_prompts)
    assert all("Direction-specific HTML rules" in prompt for prompt in html_system_prompts)


def test_create_deck_normalizes_ai_decide_and_validates_presets(isolated_graph):
    for preset_id in (None, "", "ai_decide"):
        created = decks.create_deck(
            decks.CreateDeckBody(
                deck_name=f"AI decide {preset_id}",
                expected_pages=2,
                visual_style_preset_id=preset_id,
                materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
            )
        )
        assert created["values"].get("visual_style_preset_id") is None
        assert created["values"].get("visual_style_preset_style_bias") is None

    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Preset deck",
            expected_pages=2,
            visual_style_preset_id="cultural_luxury",
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    assert created["values"]["visual_style_preset_id"] == "cultural_luxury"
    assert created["values"]["visual_style_preset_label"] == "Cultural Luxury"
    assert "premium editorial" in created["values"]["visual_style_preset_style_bias"]["tone"]

    with pytest.raises(HTTPException) as exc:
        decks.create_deck(
            decks.CreateDeckBody(
                deck_name="Bad preset",
                expected_pages=2,
                visual_style_preset_id="not_a_real_preset",
                materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
            )
        )
    assert exc.value.status_code == 400


def test_layout_review_preset_change_reruns_style(isolated_graph):
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Change direction deck",
            expected_pages=2,
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    thread_id = created["thread_id"]
    structured = hitl.resume_deck(
        thread_id,
        {
            "scenario_id": created["values"]["scenario_id"],
            "structure_id": created["values"]["structure_candidates"][0],
        },
    )
    assert _interrupt_gate(structured) == "outline"
    styled = hitl.resume_deck(thread_id, {"approved": True})
    assert _interrupt_gate(styled) == "style"
    laid_out = hitl.resume_deck(thread_id, {"approved": True})
    assert _interrupt_gate(laid_out) == "layout"

    rerouted = hitl.resume_deck(
        thread_id,
        {
            "approved": True,
            "overrides": {},
            "visual_style_preset_id": "product_clarity",
        },
    )

    assert _interrupt_gate(rerouted) == "style"
    assert rerouted["values"]["visual_style_preset_id"] == "product_clarity"
    assert rerouted["values"].get("layouts") in (None, [])
    assert not rerouted["values"].get("brief")
    assert not rerouted["values"].get("html_slides")
    assert not rerouted["values"].get("html_generation_metadata")


def test_layout_review_fork_stops_at_layout_gate_and_supports_future_forks(isolated_graph):
    source = _create_ready_deck()
    source_html = source["values"]["html_slides"][0]
    html_calls_before = len(isolated_graph["html_messages"])

    forked = history.fork_from_review(
        source["thread_id"],
        history.ForkFromLayoutBody(
            review_stage="layout",
            overrides={0: "radial_compact"},
        ),
    )

    assert forked["values"]["current_stage"] == "await_layout"
    assert _interrupt_gate(forked) == "layout"
    assert forked["values"]["layouts"][0]["pattern"] == "radial_compact"
    assert forked["values"].get("consolidated_brief_md") in (None, "")
    assert not forked["values"].get("brief")
    assert not forked["values"].get("html_slides")
    assert not forked["values"].get("html_generation_metadata")
    assert len(isolated_graph["html_messages"]) == html_calls_before

    rendered = hitl.resume_deck(
        forked["thread_id"],
        {"approved": True, "overrides": {}},
    )

    assert rendered["values"]["current_stage"] == "ready"
    assert not rendered["interrupts"]
    assert "data-pattern=\"radial_compact\"" in rendered["values"]["html_slides"][0]
    assert rendered["values"]["html_slides"][0] != source_html
    assert len(isolated_graph["html_messages"]) == html_calls_before + 2

    fork_history = history.history(rendered["thread_id"])
    assert len(fork_history["history"]) >= 4

    second_fork = history.fork_from_review(
        rendered["thread_id"],
        history.ForkFromStyleBody(
            review_stage="style",
            feedback="Make the design more restrained.",
        ),
    )
    assert second_fork["source_thread_id"] == rendered["thread_id"]
    assert _interrupt_gate(second_fork) == "style"


def test_layout_review_fork_clears_generated_image_state(isolated_graph):
    source = _create_ready_deck()
    source = _inject_stale_generated_image_state(source["thread_id"])

    forked = history.fork_from_review(
        source["thread_id"],
        history.ForkFromLayoutBody(
            review_stage="layout",
            overrides={0: "radial_compact"},
        ),
    )

    assert _interrupt_gate(forked) == "layout"
    assert [asset["asset_id"] for asset in forked["values"].get("image_assets", [])] == ["user-asset"]
    assert forked["values"].get("image_insertion_plan") in (None, {})
    assert forked["values"].get("image_insertion_status") is None
    assert forked["values"].get("image_generation_errors") in (None, [])
    assert not forked["values"].get("html_slides_base")
    assert not forked["values"].get("html_generation_metadata")

    rendered = hitl.resume_deck(
        forked["thread_id"],
        {"approved": True, "overrides": {}},
    )

    assert rendered["values"]["current_stage"] == "ready"
    assert [asset["asset_id"] for asset in rendered["values"].get("image_assets", [])] == ["user-asset"]
    assert not rendered["values"].get("image_insertion_plan")


def test_layout_review_fork_preset_change_starts_from_style(isolated_graph):
    source = _create_ready_deck()
    html_calls_before = len(isolated_graph["html_messages"])

    forked = history.fork_from_review(
        source["thread_id"],
        history.ForkFromLayoutBody(
            review_stage="layout",
            overrides={},
            visual_style_preset_id="product_clarity",
        ),
    )

    assert forked["source_thread_id"] == source["thread_id"]
    assert _interrupt_gate(forked) == "style"
    assert forked["values"]["visual_style_preset_id"] == "product_clarity"
    assert forked["values"].get("layouts") in (None, [])
    assert not forked["values"].get("brief")
    assert not forked["values"].get("html_slides")
    assert not forked["values"].get("html_generation_metadata")
    assert len(isolated_graph["html_messages"]) == html_calls_before


def test_layout_review_fork_rejects_unknown_visual_preset(isolated_graph):
    source = _create_ready_deck()

    with pytest.raises(HTTPException) as exc:
        history.fork_from_review(
            source["thread_id"],
            history.ForkFromLayoutBody(
                review_stage="layout",
                overrides={},
                visual_style_preset_id="not_a_real_preset",
            ),
        )

    assert exc.value.status_code == 400
