from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import decks, hitl, visual_playground
from app.artifacts import store
from app.graph import graph as graph_module
from app.graph.nodes import advanced_chat, html_one, layout, outline, style
from app.llm.zenmux import CompletionResult
from app.main import app


def _interrupt_gate(state: dict) -> str | None:
    interrupts = state.get("interrupts") or []
    if not interrupts:
        return None
    return interrupts[0].get("gate")


def _parse_sse_events(body: str) -> list[dict]:
    events: list[dict] = []
    for line in body.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    return events


def _advanced_draft(schema: type[advanced_chat.AdvancedChatDraft]) -> advanced_chat.AdvancedChatDraft:
    return schema(
        scenario_id="sales_pitch",
        structure_id="pyramid",
        language="en",
        summary="Lead with the recommendation, then prove it.",
        outline_slides=[
            {
                "title": "Recommendation",
                "role": "context",
                "bullets": ["Revenue is growing", "Margin is stable"],
            },
            {
                "title": "Execution path",
                "role": "execution",
                "bullets": ["Prioritize expansion", "Sequence the workstreams"],
            },
        ],
        visual_style={
            "tone": "editorial",
            "density": "balanced",
            "palette": {
                "primary": "#112233",
                "secondary": "#445566",
                "accent": "#AA5500",
                "neutral_dark": "#111111",
                "neutral_light": "#F5F5F5",
                "background": "#FFFFFF",
            },
            "typography": {
                "heading_family": "IBM Plex Serif",
                "body_family": "IBM Plex Sans",
                "display_family": "IBM Plex Serif",
                "rationale": "Strong contrast.",
            },
            "imagery_policy": "Use photographic placeholders.",
            "motion_policy": "static",
            "rationale": "Consistent with the story.",
        },
    )


@pytest.fixture()
def isolated_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    graph_module._compiled = None
    monkeypatch.setattr(graph_module, "DB_PATH", tmp_path / "threads.sqlite")
    monkeypatch.setattr(store, "ROOT", tmp_path / "threads")
    calls = {"layout": 0, "preview_html": 0, "preview_critic": 0}

    def fake_embeddings(_model: str, inputs: list[str], **_kwargs):
        return [[1.0, float(idx)] for idx, _ in enumerate(inputs)]

    def fake_chat(_model, _messages, **_kwargs):
        return "Use a pyramid story with editorial restraint."

    def style_payload(schema, primary: str = "#112233"):
        return schema(
            tone="editorial",
            density="balanced",
            palette={
                "primary": primary,
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
                        "title": "Recommendation",
                        "role": "context",
                        "bullets": ["Revenue is growing", "Margin is stable"],
                    },
                    {
                        "title": "Execution path",
                        "role": "execution",
                        "bullets": ["Prioritize expansion", "Sequence the workstreams"],
                    },
                ],
            )
        if schema is style._VisualStyle:
            return style_payload(schema)
        if schema is advanced_chat.AdvancedChatDraft:
            return _advanced_draft(schema)
        if schema is visual_playground._CandidateBatch:
            return schema(
                candidates=[
                    {
                        "label": "Editorial contrast",
                        "rationale": "Sharper hierarchy.",
                        "guidance": "Use warmer editorial contrast.",
                        "visual_style": style_payload(advanced_chat.AdvancedVisualStyle, "#663300"),
                    },
                    {
                        "label": "Product clarity",
                        "rationale": "Cleaner interface tone.",
                        "guidance": "Use lighter product clarity.",
                        "visual_style": style_payload(advanced_chat.AdvancedVisualStyle, "#003366"),
                    },
                    {
                        "label": "Strategic prestige",
                        "rationale": "Darker boardroom tone.",
                        "guidance": "Use a restrained premium palette.",
                        "visual_style": style_payload(advanced_chat.AdvancedVisualStyle, "#101820"),
                    },
                ]
            )
        if schema is html_one._HtmlCritique:
            calls["preview_critic"] += 1
            return schema(decision="accept", issues=[], revision_instructions="")
        if schema is layout._BulkSignals:
            calls["layout"] += 1
            return schema(slides=[])
        raise AssertionError(f"Unexpected schema: {schema}")

    def fake_html(_model, messages, **_kwargs):
        calls["preview_html"] += 1
        prompt = messages[1]["content"]
        title_line = next(
            (line for line in prompt.splitlines() if line.startswith("Title: ")),
            "Title: Slide",
        )
        title = title_line.removeprefix("Title: ")
        return CompletionResult(
            text=(
                "<!DOCTYPE html><html><head><style>"
                "*{box-sizing:border-box}.slide{width:960px;height:540px;overflow:hidden;position:relative;}"
                "</style></head><body>"
                f"<div class=\"slide\"><h1>{title}</h1></div>"
                "</body></html>"
            ),
            finish_reason="stop",
        )

    monkeypatch.setattr(advanced_chat.zenmux, "chat", fake_chat)
    monkeypatch.setattr(advanced_chat.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(outline.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(style.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(layout.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(html_one.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(html_one.zenmux, "chat_with_metadata", fake_html)

    from app.graph.nodes import digest as digest_mod

    monkeypatch.setattr(digest_mod.zenmux, "embeddings", fake_embeddings)

    yield calls
    graph_module._compiled = None


def _create_style_gate_deck() -> dict:
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Style deck",
            expected_pages=2,
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    structured = hitl.resume_deck(
        created["thread_id"],
        {
            "scenario_id": created["values"]["scenario_id"],
            "structure_id": created["values"]["structure_candidates"][0],
        },
    )
    assert _interrupt_gate(structured) == "outline"
    styled = hitl.resume_deck(created["thread_id"], {"approved": True})
    assert _interrupt_gate(styled) == "style"
    return styled


def test_visual_playground_rejects_normal_style_gate(isolated_graph):
    client = TestClient(app)
    styled = _create_style_gate_deck()

    response = client.post(
        f"/decks/{styled['thread_id']}/visual_playground/stream",
        json={"candidate_count": 2, "guidance": "Explore two style directions."},
    )

    assert response.status_code == 409
    assert isolated_graph["layout"] == 0


def test_visual_playground_runs_from_outline_gate_and_continues_to_layout(isolated_graph):
    client = TestClient(app)
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Outline visual playground",
            expected_pages=2,
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    structured = hitl.resume_deck(
        created["thread_id"],
        {
            "scenario_id": created["values"]["scenario_id"],
            "structure_id": created["values"]["structure_candidates"][0],
        },
    )
    assert _interrupt_gate(structured) == "outline"

    visual_stage = client.post(
        f"/decks/{created['thread_id']}/resume/stream",
        json={"payload": {"visual_playground": True}},
    )
    assert visual_stage.status_code == 200, visual_stage.text
    visual_done = next(event for event in _parse_sse_events(visual_stage.text) if event["type"] == "done")
    assert visual_done["state"]["values"]["current_stage"] == "visual_playground"
    assert not visual_done["state"]["interrupts"]

    generated = client.post(
        f"/decks/{created['thread_id']}/visual_playground/stream",
        json={"candidate_count": 1},
    )
    assert generated.status_code == 200, generated.text
    generated_done = next(event for event in _parse_sse_events(generated.text) if event["type"] == "done")
    candidate = generated_done["state"]["values"]["visual_playground_candidates"][0]
    assert generated_done["state"]["values"]["current_stage"] == "visual_playground"
    assert generated_done["state"]["values"].get("visual_style") in (None, {})
    assert isolated_graph["layout"] == 0

    selected = client.post(
        f"/decks/{created['thread_id']}/visual_playground/select",
        json={"candidate_id": candidate["candidate_id"]},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["values"]["visual_style"]["palette"]["primary"] == "#663300"
    assert selected.json()["values"]["current_stage"] == "visual_playground"

    continued = client.post(
        f"/decks/{created['thread_id']}/visual_playground/continue/stream",
        json={},
    )
    assert continued.status_code == 200, continued.text
    continued_done = next(event for event in _parse_sse_events(continued.text) if event["type"] == "done")
    assert _interrupt_gate(continued_done["state"]) == "layout"
    assert isolated_graph["layout"] == 1
    assert not continued_done["state"]["values"].get("html_slides")


def test_visual_playground_can_disable_html_critic(isolated_graph):
    client = TestClient(app)
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="No critic previews",
            expected_pages=2,
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    hitl.resume_deck(
        created["thread_id"],
        {
            "scenario_id": created["values"]["scenario_id"],
            "structure_id": created["values"]["structure_candidates"][0],
        },
    )
    visual_stage = client.post(
        f"/decks/{created['thread_id']}/resume/stream",
        json={"payload": {"visual_playground": True}},
    )
    assert visual_stage.status_code == 200, visual_stage.text

    generated = client.post(
        f"/decks/{created['thread_id']}/visual_playground/stream",
        json={"candidate_count": 1, "html_critic_enabled": False},
    )

    assert generated.status_code == 200, generated.text
    done = next(event for event in _parse_sse_events(generated.text) if event["type"] == "done")
    values = done["state"]["values"]
    assert values["visual_playground_status"]["html_critic_enabled"] is False
    assert values["visual_playground_candidates"][0]["preview_slides"]
    assert isolated_graph["preview_html"] > 0
    assert isolated_graph["preview_critic"] == 0


def test_visual_playground_can_open_creator_playground_after_selection(isolated_graph):
    client = TestClient(app)
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Visual playground to creator",
            expected_pages=2,
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    hitl.resume_deck(
        created["thread_id"],
        {
            "scenario_id": created["values"]["scenario_id"],
            "structure_id": created["values"]["structure_candidates"][0],
        },
    )
    visual_stage = client.post(
        f"/decks/{created['thread_id']}/resume/stream",
        json={"payload": {"visual_playground": True}},
    )
    assert visual_stage.status_code == 200, visual_stage.text
    generated = client.post(
        f"/decks/{created['thread_id']}/visual_playground/stream",
        json={"candidate_count": 1},
    )
    generated_done = next(event for event in _parse_sse_events(generated.text) if event["type"] == "done")
    candidate = generated_done["state"]["values"]["visual_playground_candidates"][0]
    selected = client.post(
        f"/decks/{created['thread_id']}/visual_playground/select",
        json={"candidate_id": candidate["candidate_id"]},
    )
    assert selected.status_code == 200, selected.text

    opened = client.post(
        f"/decks/{created['thread_id']}/visual_playground/continue/stream",
        json={"destination": "playground"},
    )

    assert opened.status_code == 200, opened.text
    done = next(event for event in _parse_sse_events(opened.text) if event["type"] == "done")
    assert done["state"]["values"]["current_stage"] == "playground"
    assert done["state"]["values"]["visual_style"]["palette"]["primary"] == "#663300"
    assert isolated_graph["layout"] == 0


def test_visual_playground_rejects_deck_before_style_stage(isolated_graph):
    client = TestClient(app)
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Too early",
            materials=[decks.Material(kind="text", uri="text:Revenue up.")],
        )
    )

    response = client.post(
        f"/decks/{created['thread_id']}/visual_playground/stream",
        json={"candidate_count": 1},
    )

    assert response.status_code == 409


def test_visual_playground_rejects_candidate_counts_outside_cap(isolated_graph):
    client = TestClient(app)
    styled = _create_style_gate_deck()

    too_low = client.post(
        f"/decks/{styled['thread_id']}/visual_playground/stream",
        json={"candidate_count": 0},
    )
    too_high = client.post(
        f"/decks/{styled['thread_id']}/visual_playground/stream",
        json={"candidate_count": 6},
    )

    assert too_low.status_code == 422
    assert too_high.status_code == 422


def test_visual_playground_selection_updates_only_current_visual_style(isolated_graph):
    client = TestClient(app)
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Selection deck",
            expected_pages=2,
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    hitl.resume_deck(
        created["thread_id"],
        {
            "scenario_id": created["values"]["scenario_id"],
            "structure_id": created["values"]["structure_candidates"][0],
        },
    )
    visual_stage = client.post(
        f"/decks/{created['thread_id']}/resume/stream",
        json={"payload": {"visual_playground": True}},
    )
    assert visual_stage.status_code == 200, visual_stage.text
    response = client.post(
        f"/decks/{created['thread_id']}/visual_playground/stream",
        json={"candidate_count": 2},
    )
    assert response.status_code == 200, response.text
    done = next(event for event in _parse_sse_events(response.text) if event["type"] == "done")
    candidate = done["state"]["values"]["visual_playground_candidates"][0]

    selected = client.post(
        f"/decks/{created['thread_id']}/visual_playground/select",
        json={"candidate_id": candidate["candidate_id"]},
    )

    assert selected.status_code == 200, selected.text
    values = selected.json()["values"]
    assert values["visual_playground_selected_candidate_id"] == candidate["candidate_id"]
    assert values["visual_style"]["palette"]["primary"] == "#663300"
    assert values["visual_style_md"].startswith("# Visual Style")
    assert values.get("layouts") in (None, [])
    assert values.get("brief") in (None, {})
    assert not values.get("html_slides")


def test_visual_playground_rejects_outline_chat_draft(isolated_graph):
    client = TestClient(app)
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Advanced draft",
            agent_mode="advanced",
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    assert _interrupt_gate(created) == "advanced_chat"

    response = client.post(
        f"/decks/{created['thread_id']}/visual_playground/stream",
        json={"candidate_count": 1},
    )

    assert response.status_code == 409
    assert isolated_graph["layout"] == 0
