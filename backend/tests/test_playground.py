from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import common, decks, hitl, playground
from app.artifacts import store
from app.graph import graph as graph_module
from app.graph.nodes import html_one, layout, outline, style
from app.llm.zenmux import CompletionResult
from app.main import app


def _interrupt_gate(state: dict) -> str | None:
    interrupts = state.get("interrupts") or []
    if not interrupts:
        return None
    payload = interrupts[0]
    return payload.get("gate")


def _parse_sse_events(body: str) -> list[dict]:
    events: list[dict] = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        events.append(json.loads(line[len("data:"):].strip()))
    return events


def _has_prompt(messages: list[dict[str, object]], prompt: str) -> bool:
    return any(prompt in str(message.get("content", "")) for message in messages)


@pytest.fixture()
def isolated_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    graph_module._compiled = None
    db_path = tmp_path / "threads.sqlite"
    monkeypatch.setattr(graph_module, "DB_PATH", db_path)
    monkeypatch.setattr(decks, "DB_PATH", db_path)
    monkeypatch.setattr(store, "ROOT", tmp_path / "threads")

    calls: dict[str, object] = {
        "outline_count": 0,
        "style_messages": [],
        "layout_messages": [],
        "html_messages": [],
    }

    def fake_chat_structured(_model, messages, schema, **_kwargs):
        if schema is outline._ProposedChoices:
            return schema(
                recommended_scenario_id="sales_pitch",
                candidate_structure_ids=["pyramid", "bluf"],
                rationale="fit",
            )
        if schema is outline._Outline:
            calls["outline_count"] = int(calls["outline_count"]) + 1
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
            calls["style_messages"] = messages
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
            calls["layout_messages"] = messages
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

    def fake_html(_model, messages, **_kwargs):
        calls["html_messages"] = messages
        prompt = messages[-2]["content"] if len(messages) > 1 else ""
        title_line = next((line for line in str(prompt).splitlines() if line.startswith("Title: ")), "Title: Slide")
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

    monkeypatch.setattr(outline.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(style.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(layout.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(html_one.zenmux, "chat_with_metadata", fake_html)

    yield calls
    graph_module._compiled = None


def _create_outline_gate_deck() -> dict:
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Playground deck",
            expected_pages=2,
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    return hitl.resume_deck(
        created["thread_id"],
        {
            "scenario_id": created["values"]["scenario_id"],
            "structure_id": created["values"]["structure_candidates"][0],
        },
    )


def _create_playground_base() -> dict:
    outlined = _create_outline_gate_deck()
    assert _interrupt_gate(outlined) == "outline"
    base = hitl.resume_deck(outlined["thread_id"], {"playground": True})
    assert base["values"]["current_stage"] == "playground"
    assert not base["interrupts"]
    return base


def test_outline_gate_continues_normal_flow_when_approved(isolated_graph):
    outlined = _create_outline_gate_deck()

    assert _interrupt_gate(outlined) == "outline"
    styled = hitl.resume_deck(outlined["thread_id"], {"approved": True})

    assert _interrupt_gate(styled) == "style"


def test_playground_mode_stops_base_deck_after_outline(isolated_graph):
    base = _create_playground_base()

    assert base["values"]["current_stage"] == "playground"
    assert base["next"] == []


def test_lane_creation_clones_outline_and_applies_creator_prompt(isolated_graph):
    calls = isolated_graph
    base = _create_playground_base()
    prompt = "Use a stark black-and-gold investor pitch style."
    client = TestClient(app)

    response = client.post(
        f"/decks/{base['thread_id']}/playground/lanes/stream",
        json={"creator_prompt": prompt},
    )

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    done = next(event for event in events if event["type"] == "done")
    lane_state = done["state"]

    assert calls["outline_count"] == 1
    assert lane_state["values"]["parent_thread_id"] == base["thread_id"]
    assert lane_state["values"]["creator_prompt"] == prompt
    assert _interrupt_gate(lane_state) == "style"
    assert _has_prompt(calls["style_messages"], prompt)

    laid_out = hitl.resume_deck(lane_state["thread_id"], {"approved": True})
    assert _interrupt_gate(laid_out) == "layout"
    assert _has_prompt(calls["layout_messages"], prompt)

    ready = hitl.resume_deck(lane_state["thread_id"], {"approved": True, "overrides": {}})
    assert ready["values"]["current_stage"] == "ready"
    assert ready["values"]["brief"]["creator_prompt"] == prompt
    assert _has_prompt(calls["html_messages"], prompt)


def test_stale_lane_layout_gate_can_resume_from_synthetic_interrupt(isolated_graph):
    base = _create_playground_base()
    client = TestClient(app)
    response = client.post(
        f"/decks/{base['thread_id']}/playground/lanes/stream",
        json={"creator_prompt": "Use editorial restraint."},
    )
    assert response.status_code == 200
    done = next(event for event in _parse_sse_events(response.text) if event["type"] == "done")
    lane_state = done["state"]
    lane_thread_id = lane_state["thread_id"]

    laid_out = hitl.resume_deck(lane_thread_id, {"approved": True})
    assert _interrupt_gate(laid_out) == "layout"

    graph_module.get_graph().update_state(  # type: ignore[arg-type]
        common.config_for(lane_thread_id),
        {
            "layouts": laid_out["values"]["layouts"],
            "current_stage": "await_layout",
        },
        as_node="layout",
    )
    raw_snap = graph_module.get_graph().get_state(  # type: ignore[arg-type]
        common.config_for(lane_thread_id)
    )
    assert raw_snap.values["current_stage"] == "await_layout"
    assert raw_snap.next == ("await_layout",)
    assert raw_snap.interrupts == ()

    stale_state = common.current_state(lane_thread_id)
    assert stale_state["values"]["current_stage"] == "await_layout"
    assert _interrupt_gate(stale_state) == "layout"

    resumed = client.post(
        f"/decks/{lane_thread_id}/resume/stream",
        json={
            "payload": {
                "approved": True,
                "overrides": {},
            }
        },
    )
    assert resumed.status_code == 200
    events = _parse_sse_events(resumed.text)
    done = next(event for event in events if event["type"] == "done")
    assert done["state"]["values"]["current_stage"] == "ready"
    assert len(done["state"]["values"]["html_slides"]) == 2
    assert done["state"]["values"].get("visual_style_preset_label") is None


def test_playground_enforces_five_lane_limit(isolated_graph):
    base = _create_playground_base()
    client = TestClient(app)

    for idx in range(5):
        response = client.post(
            f"/decks/{base['thread_id']}/playground/lanes/stream",
            json={"creator_prompt": f"Lane prompt {idx}"},
        )
        assert response.status_code == 200

    response = client.post(
        f"/decks/{base['thread_id']}/playground/lanes/stream",
        json={"creator_prompt": "one too many"},
    )
    assert response.status_code == 409
    assert "at most 5 lanes" in response.json()["detail"]


def test_cutoff_lane_cannot_resume(isolated_graph):
    base = _create_playground_base()
    client = TestClient(app)
    response = client.post(
        f"/decks/{base['thread_id']}/playground/lanes/stream",
        json={"creator_prompt": "Stop after style."},
    )
    lane_event = next(event for event in _parse_sse_events(response.text) if event["type"] == "lane")
    lane = lane_event["lane"]

    cutoff = playground.cutoff_playground_lane(base["thread_id"], lane["lane_id"])

    assert cutoff["lane"]["cutoff"] is True
    with pytest.raises(HTTPException) as exc_info:
        hitl.resume_deck(lane["lane_thread_id"], {"approved": True})
    assert exc_info.value.status_code == 409


def test_masterpiece_save_list_delete_deduplicates_prompt(isolated_graph):
    base = _create_playground_base()
    client = TestClient(app)
    prompt = "Make this lane cinematic but still boardroom-safe."
    response = client.post(
        f"/decks/{base['thread_id']}/playground/lanes/stream",
        json={"creator_prompt": prompt},
    )
    lane_event = next(event for event in _parse_sse_events(response.text) if event["type"] == "lane")
    lane_id = lane_event["lane"]["lane_id"]

    first = client.post(f"/decks/{base['thread_id']}/playground/lanes/{lane_id}/masterpiece")
    second = client.post(f"/decks/{base['thread_id']}/playground/lanes/{lane_id}/masterpiece")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["masterpiece"]["id"] == second.json()["masterpiece"]["id"]

    listed = client.get("/masterpieces")
    assert listed.status_code == 200
    assert listed.json()["masterpieces"] == [first.json()["masterpiece"]]
    assert set(listed.json()["masterpieces"][0]) == {"id", "prompt", "created_at"}

    deleted = client.delete(f"/masterpieces/{first.json()['masterpiece']['id']}")
    assert deleted.status_code == 200
    assert client.get("/masterpieces").json()["masterpieces"] == []
