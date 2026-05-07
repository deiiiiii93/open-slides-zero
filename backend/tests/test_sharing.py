from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import decks, owners
from app.api.common import config_for, graph
from app.artifacts import store
from app.graph import graph as graph_module
from app.main import app


@pytest.fixture()
def isolated_share_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    graph_module._compiled = None
    db_path = tmp_path / "threads.sqlite"
    monkeypatch.setattr(graph_module, "DB_PATH", db_path)
    monkeypatch.setattr(decks, "DB_PATH", db_path)
    monkeypatch.setattr(store, "ROOT", tmp_path / "threads")
    return db_path


def _owner_hash_for_client(client: TestClient) -> str:
    client.get("/identity")
    token = client.cookies.get(owners.COOKIE_NAME)
    assert token
    return owners._owner_hash(token)


def _seed_owned_deck(thread_id: str, owner_hash: str) -> None:
    graph().update_state(
        config_for(thread_id),
        {
            "thread_id": thread_id,
            "deck_name": "Shared Source",
            "current_stage": "ready",
            "aspect_ratio": "16:9",
            "materials": [{"kind": "file", "uri": "/tmp/private/source.pdf", "name": "source.pdf"}],
            "html_slides": {0: "<html><body>shared</body></html>"},
        },
        as_node="post_html",
    )
    owners.assign_deck_owner(thread_id, owner_hash)


def test_share_link_public_snapshot_and_fork_owner_isolated(
    isolated_share_graph,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("OSZ_TEST_OWNER_TOKEN", raising=False)
    client_a = TestClient(app)
    client_b = TestClient(app)
    owner_a = _owner_hash_for_client(client_a)
    owner_b = _owner_hash_for_client(client_b)
    assert owner_a != owner_b

    _seed_owned_deck("source-deck", owner_a)

    share_res = client_a.post(
        "/decks/source-deck/share",
        headers={
            "origin": "http://localhost:5174",
            "x-osz-zenmux-key": "owner-secret-key",
        },
    )
    assert share_res.status_code == 200
    share = share_res.json()
    assert share["share_url"] == f"http://localhost:5174/?share={share['share_id']}"
    assert "owner-secret-key" not in share_res.text

    assert client_b.get("/decks/source-deck").status_code == 404

    public_res = client_b.get(f"/shares/{share['share_id']}")
    assert public_res.status_code == 200
    public_text = public_res.text
    assert "owner-secret-key" not in public_text
    assert "/tmp/private/source.pdf" not in public_text
    public = public_res.json()
    assert public["deck"]["values"]["html_slides"]["0"] == "<html><body>shared</body></html>"
    assert set(public["deck"]["values"]) == {
        "thread_id",
        "deck_name",
        "current_stage",
        "aspect_ratio",
        "html_slides",
    }

    fork_res = client_b.post(
        f"/shares/{share['share_id']}/fork",
        headers={"x-osz-zenmux-key": "viewer-secret-key"},
    )
    assert fork_res.status_code == 200
    assert "owner-secret-key" not in fork_res.text
    assert "viewer-secret-key" not in fork_res.text
    fork_state = fork_res.json()["state"]
    fork_id = fork_state["thread_id"]
    assert fork_id != "source-deck"
    assert fork_state["source_thread_id"] == "source-deck"
    assert fork_state["values"]["deck_name"] == "Shared Source (fork)"

    assert client_b.get(f"/decks/{fork_id}").status_code == 200
    assert client_a.get(f"/decks/{fork_id}").status_code == 404

    with sqlite3.connect(str(isolated_share_graph)) as conn:
        row = conn.execute(
            "SELECT owner_hash FROM deck_owners WHERE thread_id = ?",
            (fork_id,),
        ).fetchone()
    assert row == (owner_b,)
