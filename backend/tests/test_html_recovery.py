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
    monkeypatch.delenv("OSZ_HTML_REACT_MAX_CYCLES", raising=False)
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
        if schema is html_one._HtmlCritique:
            return schema(decision="accept", issues=[], revision_instructions="")
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
    metadata = ready["values"]["html_generation_metadata"]
    assert set(map(int, metadata.keys())) == {0, 1}
    assert metadata[0]["status"] == "succeeded"
    assert metadata[1]["status"] == "succeeded"
    assert metadata[1]["cycles_run"] == 2
    assert metadata[1]["cycles"][0]["stop_reason"] == "truncated"
    assert metadata[1]["cycles"][1]["critic_decision"] == "accept"


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
    metadata = paused["values"]["html_generation_metadata"]
    assert metadata[0]["status"] == "succeeded"
    assert metadata[1]["status"] == "failed"
    assert metadata[1]["cycles_run"] == 2
    assert metadata[1]["cycles"][-1]["stop_reason"] == "truncated"
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
    metadata = retried["values"]["html_generation_metadata"]
    assert metadata[0]["status"] == "succeeded"
    assert metadata[1]["status"] == "succeeded"
    assert metadata[1]["cycles_run"] == 1
    assert calls["Opening argument"] == 1
    assert calls["Execution path"] == 3


def _single_slide_brief() -> dict:
    return {
        "aspect_ratio": "16:9",
        "style": {
            "palette": {"background": "#fff", "text": "#111"},
            "typography": {"heading_family": "IBM Plex Serif", "body_family": "IBM Plex Sans"},
            "tone": "editorial",
        },
        "density": "balanced",
        "language": "en",
        "slides": [
            {
                "slide_idx": 0,
                "title": "Quality slide",
                "role": "context",
                "bullets": ["Evidence one", "Evidence two"],
                "pattern": "content_card_grid",
                "zones": ["title", "proof"],
                "wireframe": "",
            }
        ],
    }


def test_html_react_accepts_first_valid_draft(monkeypatch: pytest.MonkeyPatch):
    composer_calls = 0
    critic_calls = 0

    def fake_html(_model, messages, **_kwargs):
        nonlocal composer_calls
        composer_calls += 1
        title, pattern = _prompt_metadata(messages)
        return CompletionResult(text=_good_html(title, pattern), finish_reason="stop")

    def fake_critic(_model, _messages, schema, **_kwargs):
        nonlocal critic_calls
        critic_calls += 1
        assert schema is html_one._HtmlCritique
        return schema(decision="accept", issues=[], revision_instructions="")

    monkeypatch.setattr(html_one.zenmux, "chat_with_metadata", fake_html)
    monkeypatch.setattr(html_one.zenmux, "chat_structured", fake_critic)

    result = html_one.html_one_node({"slide_idx": 0, "brief": _single_slide_brief()})

    assert 0 in result["html_slides"]
    metadata = result["html_generation_metadata"][0]
    assert metadata["status"] == "succeeded"
    assert metadata["accepted_by"] == "critic"
    assert metadata["cycles_run"] == 1
    assert metadata["cycles"][0]["critic_decision"] == "accept"
    assert composer_calls == 1
    assert critic_calls == 1


def test_html_react_can_skip_critic_when_disabled(monkeypatch: pytest.MonkeyPatch):
    composer_calls = 0

    def fake_html(_model, messages, **_kwargs):
        nonlocal composer_calls
        composer_calls += 1
        title, pattern = _prompt_metadata(messages)
        return CompletionResult(text=_good_html(title, pattern), finish_reason="stop")

    def fail_critic(*_args, **_kwargs):
        raise AssertionError("critic should not run when disabled")

    brief = {**_single_slide_brief(), "html_critic_enabled": False}
    monkeypatch.setattr(html_one.zenmux, "chat_with_metadata", fake_html)
    monkeypatch.setattr(html_one.zenmux, "chat_structured", fail_critic)

    result = html_one.html_one_node({"slide_idx": 0, "brief": brief})

    assert 0 in result["html_slides"]
    metadata = result["html_generation_metadata"][0]
    assert metadata["status"] == "succeeded"
    assert metadata["accepted_by"] == "validator"
    assert metadata["critic_model"] is None
    assert metadata["cycles_run"] == 1
    assert metadata["cycles"][0]["stop_reason"] == "validator_accept"
    assert composer_calls == 1


def test_html_react_revision_uses_critic_feedback(monkeypatch: pytest.MonkeyPatch):
    composer_messages: list[list[dict[str, object]]] = []
    critic_calls = 0

    def fake_html(_model, messages, **_kwargs):
        composer_messages.append(messages)
        title, pattern = _prompt_metadata(messages)
        suffix = " refined" if len(composer_messages) == 2 else ""
        return CompletionResult(text=_good_html(title + suffix, pattern), finish_reason="stop")

    def fake_critic(_model, _messages, schema, **_kwargs):
        nonlocal critic_calls
        critic_calls += 1
        assert schema is html_one._HtmlCritique
        if critic_calls == 1:
            return schema(
                decision="revise",
                issues=["Hierarchy is too flat."],
                revision_instructions="Improve visual hierarchy with a stronger title zone.",
            )
        return schema(decision="accept", issues=[], revision_instructions="")

    monkeypatch.setenv("OSZ_HTML_REACT_MAX_CYCLES", "3")
    monkeypatch.setattr(html_one.zenmux, "chat_with_metadata", fake_html)
    monkeypatch.setattr(html_one.zenmux, "chat_structured", fake_critic)

    result = html_one.html_one_node({"slide_idx": 0, "brief": _single_slide_brief()})

    assert "refined" in result["html_slides"][0]
    metadata = result["html_generation_metadata"][0]
    assert metadata["status"] == "succeeded"
    assert metadata["accepted_by"] == "critic"
    assert metadata["cycles_run"] == 2
    assert metadata["cycles"][0]["critic_decision"] == "revise"
    assert metadata["cycles"][0]["critic_issues"] == ["Hierarchy is too flat."]
    assert len(composer_messages) == 2
    assert "Improve visual hierarchy" in str(composer_messages[1][-1]["content"])


def test_html_react_sends_validator_warnings_to_critic(monkeypatch: pytest.MonkeyPatch):
    critic_prompt = ""

    def fake_html(_model, messages, **_kwargs):
        title, pattern = _prompt_metadata(messages)
        html = _good_html(title, pattern).replace(
            "* { box-sizing: border-box; }",
            "* { box-sizing: border-box; font-family: Inter, sans-serif; }",
        )
        return CompletionResult(text=html, finish_reason="stop")

    def fake_critic(_model, messages, schema, **_kwargs):
        nonlocal critic_prompt
        assert schema is html_one._HtmlCritique
        critic_prompt = "\n\n".join(str(message["content"]) for message in messages)
        return schema(decision="accept", issues=[], revision_instructions="")

    monkeypatch.setattr(html_one.zenmux, "chat_with_metadata", fake_html)
    monkeypatch.setattr(html_one.zenmux, "chat_structured", fake_critic)

    html_one.html_one_node({"slide_idx": 0, "brief": _single_slide_brief()})

    assert "banned_font" in critic_prompt
    assert "Inter" in critic_prompt


def test_html_react_skips_critic_for_invalid_draft(monkeypatch: pytest.MonkeyPatch):
    composer_messages: list[list[dict[str, object]]] = []
    critic_calls = 0

    def fake_html(_model, messages, **_kwargs):
        composer_messages.append(messages)
        if len(composer_messages) == 1:
            return CompletionResult(
                text="<!DOCTYPE html><html><head><style>.slide{width:960px;",
                finish_reason="length",
            )
        assert "finish_reason=length" in str(messages[-1]["content"])
        title, pattern = _prompt_metadata(messages)
        return CompletionResult(text=_good_html(title, pattern), finish_reason="stop")

    def fake_critic(_model, _messages, schema, **_kwargs):
        nonlocal critic_calls
        critic_calls += 1
        assert schema is html_one._HtmlCritique
        return schema(decision="accept", issues=[], revision_instructions="")

    monkeypatch.setenv("OSZ_HTML_REACT_MAX_CYCLES", "2")
    monkeypatch.setattr(html_one.zenmux, "chat_with_metadata", fake_html)
    monkeypatch.setattr(html_one.zenmux, "chat_structured", fake_critic)

    result = html_one.html_one_node({"slide_idx": 0, "brief": _single_slide_brief()})

    assert 0 in result["html_slides"]
    metadata = result["html_generation_metadata"][0]
    assert metadata["cycles_run"] == 2
    assert metadata["cycles"][0]["validation_error_count"] > 0
    assert metadata["cycles"][0]["critic_decision"] is None
    assert metadata["cycles"][1]["critic_decision"] == "accept"
    assert len(composer_messages) == 2
    assert critic_calls == 1


def test_html_react_max_cycles_env_default_and_legacy_fallback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OSZ_HTML_REACT_MAX_CYCLES", raising=False)
    monkeypatch.delenv("OSZ_HTML_MAX_ATTEMPTS", raising=False)
    assert html_one._html_react_max_cycles() == 3

    monkeypatch.setenv("OSZ_HTML_MAX_ATTEMPTS", "2")
    assert html_one._html_react_max_cycles() == 2

    monkeypatch.setenv("OSZ_HTML_REACT_MAX_CYCLES", "4")
    assert html_one._html_react_max_cycles() == 4
