from __future__ import annotations

from pathlib import Path

import pytest

from app.api import decks, hitl
from app.artifacts import store
from app.graph import graph as graph_module
from app.graph.nodes import html_one, layout, outline, style
from app.llm.zenmux import CompletionResult


def _interrupt_gate(state: dict) -> str | None:
    interrupts = state.get("interrupts") or []
    if not interrupts:
        return None
    payload = interrupts[0]
    return payload.get("gate")


def _good_html(title: str, pattern: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<style>
  * {{ box-sizing: border-box; }}
  .slide {{
    width: 960px;
    height: 540px;
    overflow: hidden;
    position: relative;
    background: #ffffff;
  }}
</style>
</head>
<body>
  <div class="slide">
    <h1 data-pattern="{pattern}">{title}</h1>
  </div>
</body>
</html>"""


def _prompt_metadata(messages: list[dict[str, object]]) -> tuple[str, str]:
    title = "Slide"
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        title_line = next((line for line in content.splitlines() if line.startswith("Title: ")), None)
        if title_line:
            title = title_line.removeprefix("Title: ")
            break
    system_content = str(messages[0]["content"])
    pattern_line = next((line for line in system_content.splitlines() if "Use the `" in line), "")
    pattern = pattern_line.split("`")[1] if "`" in pattern_line else "unknown"
    return title, pattern


@pytest.fixture()
def isolated_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    graph_module._compiled = None
    monkeypatch.setattr(graph_module, "DB_PATH", tmp_path / "threads.sqlite")
    monkeypatch.setattr(store, "ROOT", tmp_path / "threads")
    monkeypatch.setenv("OSZ_HTML_MAX_ATTEMPTS", "2")

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

    monkeypatch.setattr(outline.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(style.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(layout.zenmux, "chat_structured", fake_chat_structured)

    yield
    graph_module._compiled = None


def _create_html_stage(monkeypatch: pytest.MonkeyPatch, fake_html):
    monkeypatch.setattr(html_one.zenmux, "chat_with_metadata", fake_html)

    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="HTML recovery deck",
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
    return thread_id, hitl.resume_deck(thread_id, {"approved": True, "overrides": {}})


def test_html_retry_succeeds_within_single_run(isolated_graph, monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, int] = {}

    def fake_html(_model, messages, **_kwargs):
        title, pattern = _prompt_metadata(messages)
        calls[title] = calls.get(title, 0) + 1
        if title == "Execution path" and calls[title] == 1:
            return CompletionResult(text="<!DOCTYPE html><html><head><style>.slide{width:960px;", finish_reason="length")
        return CompletionResult(text=_good_html(title, pattern), finish_reason="stop")

    _, ready = _create_html_stage(monkeypatch, fake_html)

    assert ready["values"]["current_stage"] == "ready"
    assert not ready["interrupts"]
    assert calls["Opening argument"] == 1
    assert calls["Execution path"] == 2


def test_html_failure_interrupts_and_never_persists_placeholder(isolated_graph, monkeypatch: pytest.MonkeyPatch):
    def fake_html(_model, messages, **_kwargs):
        title, pattern = _prompt_metadata(messages)
        if title == "Execution path":
            return CompletionResult(text="<!DOCTYPE html><html><head><style>.slide{width:960px;", finish_reason="length")
        return CompletionResult(text=_good_html(title, pattern), finish_reason="stop")

    _, paused = _create_html_stage(monkeypatch, fake_html)

    assert paused["values"]["current_stage"] == "html"
    assert _interrupt_gate(paused) == "html"
    assert 1 not in paused["values"].get("html_slides", {})
    assert paused["values"]["html_failures"][0]["slide_idx"] == 1
    assert all("html generation failed" not in html for html in paused["values"].get("html_slides", {}).values())
    assert paused["interrupts"][0]["failed_slides"][0]["slide_idx"] == 1


def test_html_retry_interrupt_reruns_only_failed_slides(isolated_graph, monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, int] = {}

    def fake_html(_model, messages, **_kwargs):
        title, pattern = _prompt_metadata(messages)
        calls[title] = calls.get(title, 0) + 1
        if title == "Execution path" and calls[title] < 3:
            return CompletionResult(text="<!DOCTYPE html><html><head><style>.slide{width:960px;", finish_reason="length")
        return CompletionResult(text=_good_html(title, pattern), finish_reason="stop")

    thread_id, paused = _create_html_stage(monkeypatch, fake_html)
    preserved = paused["values"]["html_slides"][0]

    assert _interrupt_gate(paused) == "html"

    retried = hitl.resume_deck(thread_id, {"retry_failed": True})

    assert retried["values"]["current_stage"] == "ready"
    assert not retried["interrupts"]
    assert retried["values"]["html_slides"][0] == preserved
    assert calls["Opening argument"] == 1
    assert calls["Execution path"] == 3
