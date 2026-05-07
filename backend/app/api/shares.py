"""Deck sharing endpoints.

Share URLs expose a read-only rendered snapshot through an opaque token. Forking
copies the source checkpoint into the viewer's owner namespace so all later edits
use that viewer's request-scoped ZenMux key.
"""

from __future__ import annotations

import secrets
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..artifacts import store
from ..graph import graph as graph_module
from .common import config_for, current_state, graph, mirror_to_disk
from .history import _clone_checkpoint_lineage
from .owners import Owner, assign_deck_to_owner, current_owner, require_deck_owner

router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    graph_module.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(graph_module.DB_PATH))
    conn.row_factory = sqlite3.Row
    ensure_tables(conn)
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deck_shares (
            share_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            owner_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_deck_shares_thread
        ON deck_shares(thread_id, revoked_at)
        """
    )
    conn.commit()


def _share_url(request: Request, share_id: str) -> str:
    origin = (request.headers.get("origin") or str(request.base_url).rstrip("/")).rstrip("/")
    return f"{origin}/?share={share_id}"


def _active_share(thread_id: str, owner_hash: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT share_id, thread_id, created_at
            FROM deck_shares
            WHERE thread_id = ? AND owner_hash = ? AND revoked_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (thread_id, owner_hash),
        ).fetchone()
    return dict(row) if row else None


def _create_share(thread_id: str, owner_hash: str) -> dict[str, Any]:
    share_id = secrets.token_urlsafe(18)
    created_at = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO deck_shares (share_id, thread_id, owner_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (share_id, thread_id, owner_hash, created_at),
        )
        conn.commit()
    return {"share_id": share_id, "thread_id": thread_id, "created_at": created_at}


def _share_record(share_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT share_id, thread_id, created_at
            FROM deck_shares
            WHERE share_id = ? AND revoked_at IS NULL
            """,
            (share_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown share link")
    return dict(row)


def _public_deck_state(thread_id: str, *, source_thread_id: str | None = None) -> dict[str, Any]:
    state = current_state(thread_id, source_thread_id=source_thread_id)
    values = state.get("values") or {}
    public_values = {
        "thread_id": thread_id,
        "deck_name": values.get("deck_name") or thread_id,
        "current_stage": values.get("current_stage") or "unknown",
        "aspect_ratio": values.get("aspect_ratio") or "16:9",
        "html_slides": values.get("html_slides") or {},
    }
    return {
        "thread_id": state["thread_id"],
        "checkpoint_id": state.get("checkpoint_id"),
        "source_thread_id": source_thread_id,
        "values": public_values,
        "next": [],
        "interrupts": [],
        "recovery_hint": state.get("recovery_hint"),
        "created_at": state.get("created_at"),
    }


def _rewrite_artifact_paths(value: Any, source_root: str, target_root: str) -> Any:
    if isinstance(value, str):
        return value.replace(source_root, target_root)
    if isinstance(value, list):
        return [_rewrite_artifact_paths(item, source_root, target_root) for item in value]
    if isinstance(value, dict):
        return {
            key: _rewrite_artifact_paths(item, source_root, target_root)
            for key, item in value.items()
        }
    return value


def _copy_artifacts_and_path_patch(
    source_thread_id: str,
    new_thread_id: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    source_dir = store.thread_dir(source_thread_id)
    target_dir = store.thread_dir(new_thread_id)
    if source_dir.exists() and not target_dir.exists():
        shutil.copytree(source_dir, target_dir)

    source_root = str(source_dir.resolve())
    target_root = str(target_dir.resolve())
    patch: dict[str, Any] = {}
    for field in (
        "materials",
        "materials_digest",
        "materials_index",
        "image_assets",
        "image_insertion_plan",
        "style_reference_image_uri",
    ):
        if field in values:
            patch[field] = _rewrite_artifact_paths(values[field], source_root, target_root)
    return patch


def _fork_shared_deck(source_thread_id: str, owner: Owner) -> dict[str, Any]:
    g = graph()
    snap = g.get_state(config_for(source_thread_id))  # type: ignore[arg-type]
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown shared deck")

    new_thread_id = uuid.uuid4().hex[:12]
    values = dict(snap.values or {})
    deck_name = values.get("deck_name") or source_thread_id
    patch = {
        "thread_id": new_thread_id,
        "deck_name": f"{deck_name} (fork)",
        **_copy_artifacts_and_path_patch(source_thread_id, new_thread_id, values),
    }
    target_cfg = _clone_checkpoint_lineage(source_thread_id, snap.config, new_thread_id)
    g.update_state(target_cfg, patch)  # type: ignore[arg-type]
    assign_deck_to_owner(new_thread_id, owner)
    mirror_to_disk(new_thread_id)
    return current_state(new_thread_id, source_thread_id=source_thread_id)


@router.post("/decks/{thread_id}/share")
def create_deck_share(
    thread_id: str,
    request: Request,
    owner: Owner = Depends(current_owner),
) -> dict[str, Any]:
    require_deck_owner(thread_id, owner)
    snap = graph().get_state(config_for(thread_id))  # type: ignore[arg-type]
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown deck")

    share = _active_share(thread_id, owner.owner_hash) or _create_share(thread_id, owner.owner_hash)
    return {
        "ok": True,
        "share_id": share["share_id"],
        "thread_id": thread_id,
        "share_url": _share_url(request, share["share_id"]),
        "created_at": share["created_at"],
    }


@router.get("/shares/{share_id}")
def get_shared_deck(share_id: str) -> dict[str, Any]:
    share = _share_record(share_id)
    return {
        "share_id": share_id,
        "source_thread_id": share["thread_id"],
        "created_at": share["created_at"],
        "deck": _public_deck_state(share["thread_id"], source_thread_id=share["thread_id"]),
    }


@router.post("/shares/{share_id}/fork")
def fork_shared_deck(
    share_id: str,
    owner: Owner = Depends(current_owner),
) -> dict[str, Any]:
    share = _share_record(share_id)
    state = _fork_shared_deck(share["thread_id"], owner)
    return {"ok": True, "share_id": share_id, "state": state}
