from __future__ import annotations

from pathlib import Path

import pytest

from app.api import decks, hitl, history
from app.artifacts import store
from app.graph import graph as graph_module
from app.graph.nodes import html_one, layout, outline, style


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
        raise AssertionError(f"Unexpected schema: {schema}")

    def fake_chat(_model, messages, **_kwargs):
        prompt = messages[-1]["content"]
        title_line = next((line for line in prompt.splitlines() if line.startswith("Title: ")), "Title: Slide")
        pattern_line = next((line for line in messages[0]["content"].splitlines() if "Use the `" in line), "")
        title = title_line.removeprefix("Title: ")
        pattern = pattern_line.split("`")[1] if "`" in pattern_line else "unknown"
        return (
            "<!DOCTYPE html><html><body>"
            "<div style=\"width:960px;height:540px;overflow:hidden;box-sizing:border-box;position:relative;\">"
            f"<h1 data-pattern=\"{pattern}\">{title}</h1>"
            "</div></body></html>"
        )

    monkeypatch.setattr(outline.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(style.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(layout.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(html_one.zenmux, "chat", fake_chat)

    yield
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
    assert _interrupt_gate(structured) == "style"
    laid_out = hitl.resume_deck(thread_id, {"approved": True})
    assert _interrupt_gate(laid_out) == "layout"
    ready = hitl.resume_deck(thread_id, {"approved": True, "overrides": {}})
    assert ready["values"]["current_stage"] == "ready"
    return ready


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


def test_layout_review_fork_rerenders_html_and_supports_future_forks(isolated_graph):
    source = _create_ready_deck()
    source_html = source["values"]["html_slides"][0]

    forked = history.fork_from_review(
        source["thread_id"],
        history.ForkFromLayoutBody(
            review_stage="layout",
            overrides={0: "radial_compact"},
        ),
    )

    assert forked["values"]["current_stage"] == "ready"
    assert not forked["interrupts"]
    assert forked["values"]["layouts"][0]["pattern"] == "radial_compact"
    assert "data-pattern=\"radial_compact\"" in forked["values"]["html_slides"][0]
    assert forked["values"]["html_slides"][0] != source_html

    fork_history = history.history(forked["thread_id"])
    assert len(fork_history["history"]) >= 4

    second_fork = history.fork_from_review(
        forked["thread_id"],
        history.ForkFromStyleBody(
            review_stage="style",
            feedback="Make the design more restrained.",
        ),
    )
    assert second_fork["source_thread_id"] == forked["thread_id"]
    assert _interrupt_gate(second_fork) == "style"
