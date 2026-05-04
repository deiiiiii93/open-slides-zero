from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import comments as comments_api
from app.api import common, decks, hitl, playground, playground_store
from app.artifacts import store
from app.graph import graph as graph_module
from app.graph.nodes import html_one, layout, outline, style
from app.llm.models import get_model
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
        "style_model": None,
        "layout_model": None,
        "html_models": [],
    }

    def fake_chat_structured(model, messages, schema, **_kwargs):
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
            calls["style_model"] = model
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
            calls["layout_model"] = model
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

    def fake_html(model, messages, **_kwargs):
        calls["html_models"] = [*list(calls["html_models"]), model]
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


def _create_playground_lane(client: TestClient, base: dict, prompt: str = "Try a sharper lane.") -> dict:
    response = client.post(
        f"/decks/{base['thread_id']}/playground/lanes/stream",
        json={"creator_prompt": prompt},
    )
    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    done = next(event for event in events if event["type"] == "done")
    return done["state"]


def test_delete_deck_removes_checkpoints_and_artifacts(isolated_graph):
    client = TestClient(app)
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Delete me",
            expected_pages=2,
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    thread_id = created["thread_id"]
    artifact_dir = store.ROOT / thread_id
    store.write_markdown(thread_id, "probe.md", "temporary mirror")

    assert artifact_dir.exists()

    response = client.delete(f"/decks/{thread_id}")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "thread_id": thread_id,
        "deleted_thread_ids": [thread_id],
    }
    assert client.get(f"/decks/{thread_id}").status_code == 404
    assert thread_id not in {deck["thread_id"] for deck in client.get("/decks").json()["decks"]}
    assert not artifact_dir.exists()


def test_delete_playground_parent_cascades_lane_thread_and_records(isolated_graph):
    client = TestClient(app)
    base = _create_playground_base()
    lane_state = _create_playground_lane(client, base)
    parent_thread_id = base["thread_id"]
    lane_thread_id = lane_state["thread_id"]

    assert playground_store.get_lane_by_thread(lane_thread_id) is not None
    assert (store.ROOT / parent_thread_id).exists()
    assert (store.ROOT / lane_thread_id).exists()

    response = client.delete(f"/decks/{parent_thread_id}")

    assert response.status_code == 200
    assert response.json()["thread_id"] == parent_thread_id
    assert set(response.json()["deleted_thread_ids"]) == {parent_thread_id, lane_thread_id}
    assert client.get(f"/decks/{parent_thread_id}").status_code == 404
    assert client.get(f"/decks/{lane_thread_id}").status_code == 404
    assert playground_store.list_lanes(parent_thread_id) == []
    assert playground_store.get_lane_by_thread(lane_thread_id) is None
    assert not (store.ROOT / parent_thread_id).exists()
    assert not (store.ROOT / lane_thread_id).exists()


def test_delete_playground_lane_preserves_parent_and_removes_lane_record(isolated_graph):
    client = TestClient(app)
    base = _create_playground_base()
    lane_state = _create_playground_lane(client, base)
    parent_thread_id = base["thread_id"]
    lane_thread_id = lane_state["thread_id"]

    response = client.delete(f"/decks/{lane_thread_id}")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "thread_id": lane_thread_id,
        "deleted_thread_ids": [lane_thread_id],
    }
    assert client.get(f"/decks/{lane_thread_id}").status_code == 404
    assert client.get(f"/decks/{parent_thread_id}").status_code == 200
    assert playground_store.list_lanes(parent_thread_id) == []
    assert playground_store.get_lane_by_thread(lane_thread_id) is None
    assert (store.ROOT / parent_thread_id).exists()
    assert not (store.ROOT / lane_thread_id).exists()


def test_delete_playground_lane_endpoint_frees_selected_lane_slot(isolated_graph):
    client = TestClient(app)
    base = _create_playground_base()
    parent_thread_id = base["thread_id"]
    lanes: list[dict] = []

    for idx in range(5):
        response = client.post(
            f"/decks/{parent_thread_id}/playground/lanes/stream",
            json={"creator_prompt": f"Lane prompt {idx}"},
        )
        assert response.status_code == 200, response.text
        lane_event = next(
            event for event in _parse_sse_events(response.text) if event["type"] == "lane"
        )
        lanes.append(lane_event["lane"])

    response = client.delete(f"/decks/{parent_thread_id}/playground/lanes/{lanes[0]['lane_id']}")

    assert response.status_code == 200
    assert response.json()["thread_id"] == lanes[0]["lane_thread_id"]
    assert response.json()["deleted_thread_ids"] == [lanes[0]["lane_thread_id"]]
    assert client.get(f"/decks/{parent_thread_id}").status_code == 200
    assert client.get(f"/decks/{lanes[0]['lane_thread_id']}").status_code == 404
    assert playground_store.get_lane(parent_thread_id, lanes[0]["lane_id"]) is None
    assert len(playground_store.list_lanes(parent_thread_id)) == 4

    replacement = client.post(
        f"/decks/{parent_thread_id}/playground/lanes/stream",
        json={"creator_prompt": "Replacement lane"},
    )

    assert replacement.status_code == 200, replacement.text
    replacement_lane = next(
        event for event in _parse_sse_events(replacement.text) if event["type"] == "lane"
    )["lane"]
    assert replacement_lane["lane_id"] == lanes[0]["lane_id"]
    assert len(playground_store.list_lanes(parent_thread_id)) == 5


def test_delete_unknown_deck_returns_404(isolated_graph):
    client = TestClient(app)

    response = client.delete("/decks/missing-thread")

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown deck"


def test_outline_gate_continues_normal_flow_when_approved(isolated_graph):
    outlined = _create_outline_gate_deck()

    assert _interrupt_gate(outlined) == "outline"
    styled = hitl.resume_deck(outlined["thread_id"], {"approved": True})

    assert _interrupt_gate(styled) == "style"


def test_playground_mode_stops_base_deck_after_outline(isolated_graph):
    base = _create_playground_base()

    assert base["values"]["current_stage"] == "playground"
    assert base["next"] == []


def test_playground_model_options_endpoint(isolated_graph):
    client = TestClient(app)

    response = client.get("/playground/model-options")

    assert response.status_code == 200
    stages = response.json()["stages"]
    assert set(stages) == {"style", "layout", "html"}
    assert all(stages[stage]["options"] for stage in stages)


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


def test_lane_creation_applies_model_overrides_per_stage(isolated_graph):
    calls = isolated_graph
    base = _create_playground_base()
    client = TestClient(app)
    overrides = {
        "style": "google/gemini-3.1-pro-preview",
        "layout": "openai/gpt-5.4",
        "html": "openai/gpt-5.4-mini",
    }

    response = client.post(
        f"/decks/{base['thread_id']}/playground/lanes/stream",
        json={"creator_prompt": "Try alternate models.", "model_overrides": overrides},
    )

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    done = next(event for event in events if event["type"] == "done")
    lane_state = done["state"]

    assert lane_state["values"]["lane_model_overrides"] == overrides
    assert calls["style_model"] == overrides["style"]

    laid_out = hitl.resume_deck(lane_state["thread_id"], {"approved": True})
    assert _interrupt_gate(laid_out) == "layout"
    assert calls["layout_model"] == overrides["layout"]

    ready = hitl.resume_deck(lane_state["thread_id"], {"approved": True, "overrides": {}})
    assert ready["values"]["current_stage"] == "ready"
    assert ready["values"]["brief"]["lane_model_overrides"] == overrides
    assert set(calls["html_models"]) == {overrides["html"]}


def test_lane_creation_without_model_overrides_uses_defaults(isolated_graph):
    calls = isolated_graph
    base = _create_playground_base()
    client = TestClient(app)

    response = client.post(
        f"/decks/{base['thread_id']}/playground/lanes/stream",
        json={"creator_prompt": "Use default models."},
    )

    assert response.status_code == 200
    done = next(event for event in _parse_sse_events(response.text) if event["type"] == "done")
    lane_state = done["state"]

    assert lane_state["values"].get("lane_model_overrides") is None
    assert calls["style_model"] == get_model("style.text")

    laid_out = hitl.resume_deck(lane_state["thread_id"], {"approved": True})
    assert _interrupt_gate(laid_out) == "layout"
    assert calls["layout_model"] == get_model("layout")

    ready = hitl.resume_deck(lane_state["thread_id"], {"approved": True, "overrides": {}})
    assert ready["values"]["current_stage"] == "ready"
    assert set(calls["html_models"]) == {get_model("html")}


def test_lane_creation_rejects_invalid_model_overrides(isolated_graph):
    base = _create_playground_base()
    client = TestClient(app)

    bad_stage = client.post(
        f"/decks/{base['thread_id']}/playground/lanes/stream",
        json={"model_overrides": {"outline": "openai/gpt-5.4"}},
    )
    bad_model = client.post(
        f"/decks/{base['thread_id']}/playground/lanes/stream",
        json={"model_overrides": {"style": "unknown/provider-model"}},
    )

    assert bad_stage.status_code == 400
    assert "Unknown model override stage" in bad_stage.json()["detail"]
    assert bad_model.status_code == 400
    assert "Unknown lane model id" in bad_model.json()["detail"]


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


def test_failed_lane_resume_emits_recoverable_done_state(
    isolated_graph,
    monkeypatch: pytest.MonkeyPatch,
):
    base = _create_playground_base()
    client = TestClient(app)
    response = client.post(
        f"/decks/{base['thread_id']}/playground/lanes/stream",
        json={"creator_prompt": "Use a Kimi lane."},
    )
    assert response.status_code == 200
    done = next(event for event in _parse_sse_events(response.text) if event["type"] == "done")
    lane_thread_id = done["state"]["thread_id"]

    def fail_layout(_model, _messages, schema, **_kwargs):
        if schema is layout._BulkSignals:
            raise RuntimeError("provider rejected params")
        raise AssertionError(f"Unexpected schema: {schema}")

    monkeypatch.setattr(layout.zenmux, "chat_structured", fail_layout)

    failed = client.post(
        f"/decks/{lane_thread_id}/resume/stream",
        json={"payload": {"approved": True}},
    )

    assert failed.status_code == 200
    events = _parse_sse_events(failed.text)
    error = next(event for event in events if event["type"] == "error")
    failed_done = next(event for event in events if event["type"] == "done")
    failed_state = failed_done["state"]

    assert "provider rejected params" in error["message"]
    assert failed_state["values"]["current_stage"] == "layout"
    assert failed_state["next"] == ["layout"]
    assert failed_state["tasks"][0]["name"] == "layout"
    assert "provider rejected params" in failed_state["tasks"][0]["error"]


def test_playground_lane_comment_stays_html_only_even_if_classifier_says_layout(
    isolated_graph,
    monkeypatch: pytest.MonkeyPatch,
):
    base = _create_playground_base()
    client = TestClient(app)
    response = client.post(
        f"/decks/{base['thread_id']}/playground/lanes/stream",
        json={"creator_prompt": "Keep it visual."},
    )
    assert response.status_code == 200
    done = next(event for event in _parse_sse_events(response.text) if event["type"] == "done")
    lane_state = done["state"]
    lane_thread_id = lane_state["thread_id"]

    hitl.resume_deck(lane_thread_id, {"approved": True})
    ready = hitl.resume_deck(lane_thread_id, {"approved": True, "overrides": {}})
    original_count = len(ready["values"]["html_slides"])

    def fake_edit_intent(_state):
        return {
            "pending_edit_ops": [{
                "target_stage": "layout",
                "rationale": "classifier overreached on a position-only comment",
                "affected_slides": [0],
                "patch_fragment": {},
            }]
        }

    monkeypatch.setattr(comments_api, "edit_intent_node", fake_edit_intent)

    response = client.post(
        f"/decks/{lane_thread_id}/slides/0/comments/stream",
        json={
            "text": "move this visual block a little lower",
            "box": {"x": 0, "y": 0, "w": 1, "h": 1},
        },
    )

    assert response.status_code == 200
    done = next(event for event in _parse_sse_events(response.text) if event["type"] == "done")
    state = done["state"]
    assert state["values"]["current_stage"] == "ready"
    assert state["next"] == []
    assert not state["interrupts"]
    assert len(state["values"]["html_slides"]) == original_count
    assert done["result"]["coerced_stage"] == "html"
    assert done["result"]["regen"]["from_stage"] == "html"


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
