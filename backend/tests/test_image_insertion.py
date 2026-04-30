from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import pytest
import httpx
from fastapi.testclient import TestClient

from app.api import decks, images
from app.artifacts import store
from app.graph import graph as graph_module
from app.graph.nodes import image_insert
from app.main import app


def _slide_html() -> str:
    return """<!DOCTYPE html>
<html><body>
<div style="width:960px;height:540px;position:relative">
  <h1>Keep this copy</h1>
  <div data-image-placeholder="true" data-prompt-hint="Product photo" style="position:absolute;left:10px;top:20px;width:300px;height:180px;border:1px dashed #999">
    <span>Add image here</span>
    <span>Suggested: Product photo</span>
  </div>
</div>
</body></html>"""


def test_proxy_image_returns_same_origin_image_bytes(monkeypatch: pytest.MonkeyPatch):
    request = httpx.Request("GET", "https://cdn.example.org/photo.jpg")
    response = httpx.Response(
        200,
        headers={"content-type": "image/jpeg"},
        content=b"jpg-bytes",
        request=request,
    )

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url: str):
            assert url == "https://cdn.example.org/photo.jpg"
            return response

    monkeypatch.setattr(images, "_is_public_http_url", lambda _url: True)
    monkeypatch.setattr(images.httpx, "Client", FakeClient)

    proxied = images.proxy_image("https://cdn.example.org/photo.jpg")

    assert proxied.media_type == "image/jpeg"
    assert proxied.body == b"jpg-bytes"


def test_image_materials_populate_assets_without_dropping_ocr_text(tmp_path: Path):
    image_path = tmp_path / "product.png"
    image_path.write_bytes(b"fake")
    materials = [
        {
            "kind": "image",
            "uri": str(image_path),
            "name": "product.png",
            "parsed": "Visual summary: red product on white background",
        }
    ]

    assets = image_insert.build_image_assets(materials, "thread-1")

    assert len(assets) == 1
    assert assets[0]["name"] == "product.png"
    assert assets[0]["summary"] == "Visual summary: red product on white background"
    assert assets[0]["source"] == "user"


def test_markdown_image_refs_and_gbif_pages_become_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    material_path = tmp_path / "materials" / "source.md"
    photo_path = tmp_path / "photos" / "product.jpg"
    material_path.parent.mkdir()
    photo_path.parent.mkdir()
    material_path.write_text("source", encoding="utf-8")
    photo_path.write_bytes(b"jpg")

    monkeypatch.setattr(
        image_insert,
        "_gbif_media_url",
        lambda occurrence_id: f"https://cdn.example.org/{occurrence_id}.jpg",
    )
    materials = [
        {
            "kind": "file",
            "uri": str(material_path),
            "name": "source.md",
            "parsed": (
                "![local](../photos/product.jpg)\n"
                "https://www.gbif.org/occurrence/5938200072\n"
                "https://commons.wikimedia.org/wiki/File:Powdery_mildew_9.jpg"
            ),
        }
    ]

    assets = image_insert.build_image_assets(materials, "thread-1")
    uris = {asset["uri"] for asset in assets}

    assert str(photo_path) in uris
    assert "https://cdn.example.org/5938200072.jpg" in uris
    assert (
        "https://commons.wikimedia.org/wiki/Special:Redirect/file/Powdery_mildew_9.jpg"
        in uris
    )


def test_extract_placeholder_slots_reads_hint_and_position():
    slots = image_insert.extract_placeholder_slots({0: _slide_html()})

    assert slots == [
        {
            "slot_id": "slide-0-slot-0",
            "slide_idx": 0,
            "slot_index": 0,
            "tag": "div",
            "hint": "Product photo",
            "style": "position:absolute;left:10px;top:20px;width:300px;height:180px;border:1px dashed #999",
            "attrs": {
                "data-image-placeholder": "true",
                "data-prompt-hint": "Product photo",
                "style": "position:absolute;left:10px;top:20px;width:300px;height:180px;border:1px dashed #999",
            },
        }
    ]


def test_plan_proposes_mapping_without_mutating_html(monkeypatch: pytest.MonkeyPatch):
    def fake_chat_structured(_model, _messages, schema, **_kwargs):
        return schema(
            mappings=[
                {
                    "slot_id": "slide-0-slot-0",
                    "asset_id": "asset-0",
                    "confidence": 0.91,
                    "rationale": "name matches",
                }
            ]
        )

    monkeypatch.setattr(image_insert.zenmux, "chat_structured", fake_chat_structured)
    values = {
        "thread_id": "thread-1",
        "html_slides": {0: _slide_html()},
        "image_assets": [{"asset_id": "asset-0", "uri": "https://example.com/product.png", "name": "product.png"}],
    }

    plan = image_insert.create_image_insertion_plan(values)

    assert values["html_slides"][0] == _slide_html()
    assert plan["mappings"][0]["asset_id"] == "asset-0"
    assert plan["unmatched_slots"] == []


def test_apply_mapping_preserves_copy_and_base_html():
    values = {
        "thread_id": "thread-1",
        "html_slides": {0: _slide_html()},
        "image_assets": [{"asset_id": "asset-0", "uri": "https://example.com/product.png", "name": "product.png"}],
        "image_insertion_plan": {"slots": [], "mappings": []},
    }

    update = image_insert.apply_image_mappings(
        values,
        [{"slot_id": "slide-0-slot-0", "asset_id": "asset-0"}],
    )

    assert update["html_slides_base"][0] == _slide_html()
    assert "Keep this copy" in update["html_slides"][0]
    assert 'src="https://example.com/product.png"' in update["html_slides"][0]
    assert 'data-image-placeholder="true"' not in update["html_slides"][0]
    assert update["image_insertion_status"] == "applied"


@pytest.fixture()
def isolated_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    graph_module._compiled = None
    db_path = tmp_path / "threads.sqlite"
    monkeypatch.setattr(graph_module, "DB_PATH", db_path)
    monkeypatch.setattr(decks, "DB_PATH", db_path)
    monkeypatch.setattr(store, "ROOT", tmp_path / "threads")
    yield
    graph_module._compiled = None


def test_generate_endpoint_calls_image_model_only_after_confirm(
    isolated_graph, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[str] = []

    def fake_generate(prompt: str, output_path: str | Path, **_kwargs):
        calls.append(prompt)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return {"path": str(path), "mime_type": "image/png", "model": "fake-image"}

    monkeypatch.setattr(image_insert.image_gen, "generate_image", fake_generate)
    client = TestClient(app)
    thread_id = "thread-1"
    graph_module.get_graph().update_state(
        {"configurable": {"thread_id": thread_id}},
        {
            "thread_id": thread_id,
            "current_stage": "ready",
            "materials": [],
            "html_slides": {0: _slide_html()},
            "image_assets": [],
            "image_insertion_plan": image_insert.create_image_insertion_plan({
                "thread_id": thread_id,
                "html_slides": {0: _slide_html()},
                "image_assets": [],
            }),
        },
    )

    plan_response = client.post(f"/decks/{thread_id}/images/plan")
    assert plan_response.status_code == 200
    assert calls == []

    generate_response = client.post(
        f"/decks/{thread_id}/images/generate",
        json={
            "slide_idx": 0,
            "slot_id": "slide-0-slot-0",
            "prompt": "A clean product photo",
        },
    )

    assert generate_response.status_code == 200
    assert calls == ["A clean product photo"]
    html = generate_response.json()["state"]["values"]["html_slides"]["0"]
    assert 'data-inserted-image="true"' in html
    assert generate_response.json()["state"]["values"]["html_slides_base"]["0"] == _slide_html()


def test_generate_batch_endpoint_serially_generates_multiple_slots(
    isolated_graph, monkeypatch: pytest.MonkeyPatch
):
    calls: list[str] = []

    def fake_generate(prompt: str, output_path: str | Path, **_kwargs):
        calls.append(prompt)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return {"path": str(path), "mime_type": "image/png", "model": "fake-image"}

    monkeypatch.setattr(image_insert.image_gen, "generate_image", fake_generate)
    client = TestClient(app)
    thread_id = "thread-batch"
    graph_module.get_graph().update_state(
        {"configurable": {"thread_id": thread_id}},
        {
            "thread_id": thread_id,
            "current_stage": "ready",
            "materials": [],
            "html_slides": {0: _slide_html(), 1: _slide_html()},
            "image_assets": [],
            "image_insertion_plan": image_insert.create_image_insertion_plan({
                "thread_id": thread_id,
                "html_slides": {0: _slide_html(), 1: _slide_html()},
                "image_assets": [],
            }),
        },
    )

    response = client.post(
        f"/decks/{thread_id}/images/generate_batch",
        json={
            "items": [
                {"slide_idx": 0, "slot_id": "slide-0-slot-0", "prompt": "First image"},
                {"slide_idx": 1, "slot_id": "slide-1-slot-0", "prompt": "Second image"},
            ]
        },
    )

    assert response.status_code == 200
    assert calls == ["First image", "Second image"]
    body = response.json()
    assert len(body["assets"]) == 2
    assert 'data-inserted-image="true"' in body["state"]["values"]["html_slides"]["0"]
    assert 'data-inserted-image="true"' in body["state"]["values"]["html_slides"]["1"]


def test_parallel_generate_requests_are_serialized_by_thread(
    isolated_graph, monkeypatch: pytest.MonkeyPatch
):
    calls: list[str] = []
    calls_lock = Lock()
    active = 0
    max_active = 0

    def fake_generate(prompt: str, output_path: str | Path, **_kwargs):
        nonlocal active, max_active
        with calls_lock:
            calls.append(prompt)
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(prompt.encode("utf-8"))
            return {"path": str(path), "mime_type": "image/png", "model": "fake-image"}
        finally:
            with calls_lock:
                active -= 1

    monkeypatch.setattr(image_insert.image_gen, "generate_image", fake_generate)
    client = TestClient(app)
    thread_id = "thread-parallel"
    graph_module.get_graph().update_state(
        {"configurable": {"thread_id": thread_id}},
        {
            "thread_id": thread_id,
            "current_stage": "ready",
            "materials": [],
            "html_slides": {0: _slide_html(), 1: _slide_html()},
            "image_assets": [],
            "image_insertion_plan": image_insert.create_image_insertion_plan({
                "thread_id": thread_id,
                "html_slides": {0: _slide_html(), 1: _slide_html()},
                "image_assets": [],
            }),
        },
    )

    def post_generate(slot_id: str, slide_idx: int, prompt: str):
        return client.post(
            f"/decks/{thread_id}/images/generate",
            json={"slide_idx": slide_idx, "slot_id": slot_id, "prompt": prompt},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda args: post_generate(*args),
                [
                    ("slide-0-slot-0", 0, "First image"),
                    ("slide-1-slot-0", 1, "Second image"),
                ],
            )
        )

    assert [response.status_code for response in responses] == [200, 200]
    assert sorted(calls) == ["First image", "Second image"]
    assert max_active == 1
    state = client.get(f"/decks/{thread_id}").json()["values"]
    assert 'data-inserted-image="true"' in state["html_slides"]["0"]
    assert 'data-inserted-image="true"' in state["html_slides"]["1"]
    assert len(state["image_assets"]) == 2
