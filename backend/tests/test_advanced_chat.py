from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import advanced_chat as advanced_chat_api
from app.api import comments as comments_api
from app.api import common, decks, history, hitl
from app.artifacts import store
from app.graph import graph as graph_module
from app.graph.nodes import advanced_chat, html_one, layout, outline, style
from app.llm.stream import push_token
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


def _draft(schema: type[advanced_chat.AdvancedChatDraft]) -> advanced_chat.AdvancedChatDraft:
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


def _placeholder_html(title: str = "Recommendation") -> str:
    return f"""<!DOCTYPE html><html><head><style>
*{{box-sizing:border-box}}
</style></head><body>
<div class="slide" style="width:960px;height:540px;overflow:hidden;position:relative;box-sizing:border-box">
  <h1 style="font-size:36px">{title}</h1>
  <div data-image-placeholder="true" data-prompt-hint="Hero background" style="position:absolute;left:0px;top:0px;width:960px;height:540px;display:flex;align-items:center;justify-content:center;border:1px dashed #999;background:#eee;box-sizing:border-box">
    <span>Add image here</span>
    <span>Suggested: Hero background</span>
  </div>
</div>
</body></html>"""


@pytest.fixture()
def isolated_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    graph_module._compiled = None
    monkeypatch.setattr(graph_module, "DB_PATH", tmp_path / "threads.sqlite")
    monkeypatch.setattr(store, "ROOT", tmp_path / "threads")

    calls = {"advanced_chat": 0, "style": 0, "layout": 0}

    def fake_embeddings(_model: str, inputs: list[str], **_kwargs):
        return [[1.0, float(idx)] for idx, _ in enumerate(inputs)]

    def fake_chat(_model, _messages, **kwargs):
        text = (
            "## Draft\n"
            "Use a pyramid story with editorial restraint.\n\n"
            "For the next decision, choose:\n"
            "- **(A) Keep the pyramid recommendation** as the backbone\n"
            "- **(B) Make it more visual and editorial** before layout"
        )
        if kwargs.get("stream"):
            push_token(text)
        calls["advanced_chat"] += 1
        return text

    def fake_chat_structured(_model, _messages, schema, **_kwargs):
        assert _messages[-1]["role"] == "user"
        if schema is outline._ProposedChoices:
            return schema(
                recommended_scenario_id="sales_pitch",
                candidate_structure_ids=["pyramid", "bluf"],
                rationale="fit",
            )
        if schema is advanced_chat.AdvancedChatDraft:
            return _draft(schema)
        if schema.__name__ == "_VisualStyle":
            calls["style"] += 1
            raise AssertionError("advanced mode should not run style_node before layout")
        if schema is layout._BulkSignals:
            calls["layout"] += 1
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
                        "notes": "Narrative works.",
                    },
                ],
            )
        raise AssertionError(f"Unexpected schema: {schema}")

    def fake_html(_model, messages, **_kwargs):
        prompt = messages[-1]["content"]
        title_line = next(
            (line for line in prompt.splitlines() if line.startswith("Title: ")),
            "Title: Slide",
        )
        title = title_line.removeprefix("Title: ")
        return CompletionResult(
            text=(
                "<!DOCTYPE html><html><head><style>"
                "*{box-sizing:border-box}.slide{width:960px;height:540px;overflow:hidden;}"
                "</style></head><body>"
                f"<div class=\"slide\"><h1>{title}</h1></div>"
                "</body></html>"
            ),
            finish_reason="stop",
        )

    monkeypatch.setattr(advanced_chat_api.zenmux, "chat", fake_chat)
    monkeypatch.setattr(advanced_chat_api.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(outline.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(layout.zenmux, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(html_one.zenmux, "chat_with_metadata", fake_html)

    # digest imports the same module object, but patch explicitly for readability.
    from app.graph.nodes import digest as digest_mod

    monkeypatch.setattr(digest_mod.zenmux, "embeddings", fake_embeddings)

    yield calls
    graph_module._compiled = None


def test_default_mode_still_reaches_structure_gate(isolated_graph):
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Default deck",
            materials=[decks.Material(kind="text", uri="text:Revenue up.")],
        )
    )

    assert _interrupt_gate(created) == "structure"
    assert created["values"]["current_stage"] == "await_structure"


def test_advanced_mode_reaches_advanced_chat_gate(isolated_graph):
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Advanced deck",
            agent_mode="advanced",
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )

    assert _interrupt_gate(created) == "advanced_chat"
    assert created["values"]["current_stage"] == "advanced_chat"
    assert created["values"]["materials_index"]
    assert [m["role"] for m in created["values"]["advanced_chat_messages"]] == ["assistant"]
    assert "pyramid story" in created["values"]["advanced_chat_messages"][0]["content"]
    choices = created["values"]["advanced_chat_messages"][0]["choices"]
    assert [choice["label"] for choice in choices][:2] == ["A", "B"]
    assert all(choice["message"] for choice in choices)
    assert created["values"]["advanced_chat_draft"]["outline_md"].startswith("# Outline")


def test_advanced_chat_stream_updates_transcript_draft_and_synthetic_gate(isolated_graph):
    client = TestClient(app)
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Advanced deck",
            agent_mode="advanced",
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )

    response = client.post(
        f"/decks/{created['thread_id']}/advanced_chat/stream",
        json={"message": "Make this a crisp investor update."},
    )

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert any(
        event["type"] == "token" and event["tag"] == "advanced_chat"
        for event in events
    )
    done = next(event for event in events if event["type"] == "done")
    values = done["state"]["values"]
    assert [m["role"] for m in values["advanced_chat_messages"]] == [
        "assistant",
        "user",
        "assistant",
    ]
    latest = values["advanced_chat_messages"][-1]
    assert latest["choices"]
    assert latest["choices"][0]["label"] == "A"
    assert values["advanced_chat_draft"]["outline_md"].startswith("# Outline")
    assert values["advanced_chat_draft"]["visual_style_md"].startswith("# Visual Style")

    graph_module.get_graph().update_state(  # type: ignore[arg-type]
        common.config_for(created["thread_id"]),
        {
            "advanced_chat_messages": values["advanced_chat_messages"],
            "advanced_chat_draft": values["advanced_chat_draft"],
            "current_stage": "advanced_chat",
        },
        as_node="advanced_chat",
    )
    stale_state = common.current_state(created["thread_id"])
    assert _interrupt_gate(stale_state) == "advanced_chat"


def test_choice_parser_extracts_ab_markdown_options():
    choices = advanced_chat.choices_from_text(
        """
        For Slide 1, choose:
        > - **(A) The book's central question** as the headline — intellectual, inviting
        > - **(B) A personal line from the presenter** — warmer and more intimate
        """
    )

    assert [choice["label"] for choice in choices] == ["A", "B"]
    assert choices[0]["message"].startswith("Choose option A:")
    assert "central question" in choices[0]["message"]


def test_advanced_chat_persists_user_and_assistant_when_draft_extraction_fails(
    isolated_graph,
    monkeypatch: pytest.MonkeyPatch,
):
    client = TestClient(app)
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Advanced deck",
            agent_mode="advanced",
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    original_draft = created["values"]["advanced_chat_draft"]

    def fail_structured(*_args, **_kwargs):
        raise RuntimeError("provider rejected extraction")

    monkeypatch.setattr(advanced_chat_api.zenmux, "chat_structured", fail_structured)

    response = client.post(
        f"/decks/{created['thread_id']}/advanced_chat/stream",
        json={"message": "Make it more visual."},
    )

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    done = next(event for event in events if event["type"] == "done")
    values = done["state"]["values"]
    assert [m["role"] for m in values["advanced_chat_messages"]] == [
        "assistant",
        "user",
        "assistant",
    ]
    assert values["advanced_chat_messages"][1]["content"] == "Make it more visual."
    assert values["advanced_chat_draft"] == original_draft


def test_mixed_advanced_chat_interrupt_serializes_as_advanced_chat(isolated_graph):
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Advanced deck",
            agent_mode="advanced",
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    thread_id = created["thread_id"]
    graph_module.get_graph().update_state(  # type: ignore[arg-type]
        common.config_for(thread_id),
        {"current_stage": "layout"},
        as_node="advanced_chat",
    )

    state = common.current_state(thread_id)
    assert _interrupt_gate(state) == "advanced_chat"
    assert state["values"]["current_stage"] == "advanced_chat"


def test_late_chat_persist_does_not_reopen_after_layout_started(isolated_graph):
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Advanced deck",
            agent_mode="advanced",
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    thread_id = created["thread_id"]
    laid_out = hitl.resume_deck(
        thread_id,
        {"approved": True, "draft": created["values"]["advanced_chat_draft"]},
    )
    assert _interrupt_gate(laid_out) == "layout"

    result = advanced_chat_api._persist_chat_state(
        thread_id,
        messages=[{"role": "assistant", "content": "late chat update"}],
        draft=created["values"]["advanced_chat_draft"],
    )

    assert result is None
    assert _interrupt_gate(common.current_state(thread_id)) == "layout"


def test_comments_rejected_before_ready(isolated_graph):
    client = TestClient(app)
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Advanced deck",
            agent_mode="advanced",
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )

    response = client.post(
        f"/decks/{created['thread_id']}/slides/0/comments/stream",
        json={"text": "move the background", "box": {"x": 0, "y": 0, "w": 1, "h": 1}},
    )

    assert response.status_code == 409


def test_advanced_ready_comment_stays_html_only_even_if_classifier_says_layout(
    isolated_graph,
    monkeypatch: pytest.MonkeyPatch,
):
    client = TestClient(app)
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Advanced deck",
            agent_mode="advanced",
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    laid_out = hitl.resume_deck(
        created["thread_id"],
        {"approved": True, "draft": created["values"]["advanced_chat_draft"]},
    )
    ready = hitl.resume_deck(
        created["thread_id"],
        {"approved": True, "overrides": {}},
    )
    original_count = len(ready["values"]["html_slides"])

    def fake_edit_intent(_state):
        return {
            "pending_edit_ops": [{
                "target_stage": "layout",
                "rationale": "classifier overreached on a z-index comment",
                "affected_slides": [0],
                "patch_fragment": {},
            }]
        }

    monkeypatch.setattr(comments_api, "edit_intent_node", fake_edit_intent)

    response = client.post(
        f"/decks/{created['thread_id']}/slides/0/comments/stream",
        json={
            "text": "the background image should be at the bottom layer",
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
    assert laid_out["values"]["current_stage"] == "await_layout"


def test_advanced_comment_reapplies_existing_image_mapping(
    isolated_graph,
    monkeypatch: pytest.MonkeyPatch,
):
    client = TestClient(app)
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Advanced deck",
            agent_mode="advanced",
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    hitl.resume_deck(
        created["thread_id"],
        {"approved": True, "draft": created["values"]["advanced_chat_draft"]},
    )
    ready = hitl.resume_deck(
        created["thread_id"],
        {"approved": True, "overrides": {}},
    )
    base_html = _placeholder_html("Original")
    inserted_html = base_html.replace(
        '<div data-image-placeholder="true" data-prompt-hint="Hero background" style="position:absolute;left:0px;top:0px;width:960px;height:540px;display:flex;align-items:center;justify-content:center;border:1px dashed #999;background:#eee;box-sizing:border-box">\n    <span>Add image here</span>\n    <span>Suggested: Hero background</span>\n  </div>',
        '<img src="https://example.com/hero.png" alt="Hero background" style="position:absolute;left:0px;top:0px;width:960px;height:540px;object-fit:cover" data-inserted-image="true" data-image-asset-id="asset-hero" />',
    )
    graph_module.get_graph().update_state(  # type: ignore[arg-type]
        common.config_for(created["thread_id"]),
        {
            "html_slides_base": {0: base_html},
            "html_slides": {0: inserted_html},
            "image_assets": [{
                "asset_id": "asset-hero",
                "uri": "https://example.com/hero.png",
                "name": "hero.png",
            }],
            "image_insertion_plan": {
                "status": "applied",
                "slots": [],
                "mappings": [],
                "applied_mappings": [{"slot_id": "slide-0-slot-0", "asset_id": "asset-hero"}],
                "unmatched_slots": [],
            },
            "image_insertion_status": "applied",
            "current_stage": "ready",
        },
        as_node="post_html",
    )

    monkeypatch.setattr(
        comments_api,
        "edit_intent_node",
        lambda _state: {
            "pending_edit_ops": [{
                "target_stage": "html",
                "rationale": "z-index only",
                "affected_slides": [0],
                "patch_fragment": {},
            }]
        },
    )

    def fake_regenerated_html(_model, _messages, **_kwargs):
        return CompletionResult(text=_placeholder_html("Regenerated"), finish_reason="stop")

    monkeypatch.setattr(html_one.zenmux, "chat_with_metadata", fake_regenerated_html)

    response = client.post(
        f"/decks/{created['thread_id']}/slides/0/comments/stream",
        json={
            "text": "the background image should stay at the bottom layer",
            "box": {"x": 0, "y": 0, "w": 1, "h": 1},
        },
    )

    assert response.status_code == 200
    done = next(event for event in _parse_sse_events(response.text) if event["type"] == "done")
    html = done["state"]["values"]["html_slides"]["0"]
    base = done["state"]["values"]["html_slides_base"]["0"]
    assert done["state"]["values"]["current_stage"] == "ready"
    assert "Regenerated" in base
    assert 'data-inserted-image="true"' in html
    assert 'src="https://example.com/hero.png"' in html
    assert 'data-image-placeholder="true"' not in html
    assert ready["values"]["current_stage"] == "ready"


def test_advanced_ready_style_fork_uses_style_fallback(
    isolated_graph,
    monkeypatch: pytest.MonkeyPatch,
):
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Advanced deck",
            agent_mode="advanced",
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    hitl.resume_deck(
        created["thread_id"],
        {"approved": True, "draft": created["values"]["advanced_chat_draft"]},
    )
    ready = hitl.resume_deck(
        created["thread_id"],
        {"approved": True, "overrides": {}},
    )
    graph_module.get_graph().update_state(  # type: ignore[arg-type]
        common.config_for(created["thread_id"]),
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
                "applied_mappings": [{"slot_id": "slide-0-slot-0", "asset_id": "generated-slide-0"}],
            },
            "image_insertion_status": "applied",
        },
        as_node="post_html",
    )
    ready = decks.get_deck(created["thread_id"])

    def fake_style_structured(_model, messages, schema, **_kwargs):
        assert schema is style._VisualStyle
        assert "warmer palette" in messages[1]["content"]
        return schema(
            tone="editorial",
            density="balanced",
            palette={
                "primary": "#663300",
                "secondary": "#332211",
                "accent": "#CC8844",
                "neutral_dark": "#111111",
                "neutral_light": "#FFF7ED",
                "background": "#FFFFFF",
            },
            typography={
                "heading_family": "IBM Plex Serif",
                "body_family": "IBM Plex Sans",
                "display_family": "IBM Plex Serif",
                "rationale": "Warmer editorial tone.",
            },
            imagery_policy="Use warm editorial imagery.",
            motion_policy="static",
            rationale="Reflects the fork feedback.",
        )

    monkeypatch.setattr(style.zenmux, "chat_structured", fake_style_structured)

    forked = history.fork_from_review(
        ready["thread_id"],
        history.ForkFromStyleBody(
            review_stage="style",
            feedback="Use a warmer palette.",
        ),
    )

    assert forked["source_thread_id"] == ready["thread_id"]
    assert forked["thread_id"] != ready["thread_id"]
    assert _interrupt_gate(forked) == "style"
    assert forked["values"]["visual_style_preference"] == "Use a warmer palette."
    assert forked["values"]["visual_style"]["palette"]["primary"] == "#663300"
    assert not forked["values"].get("layouts")
    assert not forked["values"].get("html_slides")
    assert [asset["asset_id"] for asset in forked["values"].get("image_assets", [])] == ["user-asset"]
    assert not forked["values"].get("image_insertion_plan")


def test_advanced_chat_commit_goes_directly_to_layout_gate(isolated_graph):
    created = decks.create_deck(
        decks.CreateDeckBody(
            deck_name="Advanced deck",
            agent_mode="advanced",
            materials=[decks.Material(kind="text", uri="text:Revenue up. Margin stable.")],
        )
    )
    client = TestClient(app)
    response = client.post(
        f"/decks/{created['thread_id']}/advanced_chat/stream",
        json={"message": "Use a pyramid structure and editorial style."},
    )
    done = next(event for event in _parse_sse_events(response.text) if event["type"] == "done")
    draft = done["state"]["values"]["advanced_chat_draft"]

    laid_out = hitl.resume_deck(
        created["thread_id"],
        {"approved": True, "draft": draft},
    )

    assert _interrupt_gate(laid_out) == "layout"
    assert laid_out["values"]["scenario_id"] == "sales_pitch"
    assert laid_out["values"]["structure_id"] == "pyramid"
    assert laid_out["values"]["outline_slides"]
    assert laid_out["values"]["visual_style"]
    assert isolated_graph["style"] == 0
    assert isolated_graph["layout"] == 1
