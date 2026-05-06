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
def isolated_owner_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
            "deck_name": f"Deck {thread_id}",
            "current_stage": "ready",
            "html_slides": {0: "<html><body>ok</body></html>"},
        },
        as_node="post_html",
    )
    owners.assign_deck_owner(thread_id, owner_hash)


def test_owner_cookie_hash_only_and_deck_access_isolated(isolated_owner_graph, monkeypatch):
    monkeypatch.delenv("OSZ_TEST_OWNER_TOKEN", raising=False)
    client_a = TestClient(app)
    client_b = TestClient(app)
    owner_a = _owner_hash_for_client(client_a)
    owner_b = _owner_hash_for_client(client_b)
    assert owner_a != owner_b

    _seed_owned_deck("deck-a", owner_a)

    assert client_a.get("/decks/deck-a").status_code == 200
    assert client_b.get("/decks/deck-a").status_code == 404

    listed_a = {item["thread_id"] for item in client_a.get("/decks").json()["decks"]}
    listed_b = {item["thread_id"] for item in client_b.get("/decks").json()["decks"]}
    assert "deck-a" in listed_a
    assert "deck-a" not in listed_b

    with sqlite3.connect(str(isolated_owner_graph)) as conn:
        row = conn.execute("SELECT owner_hash FROM deck_owners WHERE thread_id = ?", ("deck-a",)).fetchone()
    assert row == (owner_a,)
    assert client_a.cookies.get(owners.COOKIE_NAME) != owner_a


def test_legacy_unowned_deck_is_hidden(isolated_owner_graph):
    graph().update_state(
        config_for("legacy"),
        {"thread_id": "legacy", "deck_name": "Legacy", "current_stage": "ready"},
        as_node="post_html",
    )
    client = TestClient(app)

    assert client.get("/decks/legacy").status_code == 404
    assert client.get("/decks").json()["decks"] == []
