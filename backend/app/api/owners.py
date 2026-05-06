"""Anonymous deck ownership for the public app.

Users do not have accounts. A browser receives a persistent random owner token
in an HttpOnly cookie, and the server stores only its SHA-256 hash. Deck APIs
must require a matching owner row before reading or mutating a thread.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from fastapi import APIRouter, HTTPException, Request, Response

from ..graph import graph as graph_module

COOKIE_NAME = "osz_owner"
COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 400
TOKEN_BYTES = 32

router = APIRouter()


@dataclass(frozen=True)
class Owner:
    token: str
    owner_hash: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    if token := os.getenv("OSZ_TEST_OWNER_TOKEN"):
        return token
    return secrets.token_urlsafe(TOKEN_BYTES)


def _cookie_secure() -> bool:
    return os.getenv("OSZ_OWNER_COOKIE_SECURE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _connect() -> sqlite3.Connection:
    graph_module.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(graph_module.DB_PATH))
    conn.row_factory = sqlite3.Row
    ensure_tables(conn)
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deck_owners (
            thread_id TEXT PRIMARY KEY,
            owner_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_deck_owners_owner
        ON deck_owners(owner_hash, created_at)
        """
    )
    conn.commit()


def current_owner(request: Request, response: Response) -> Owner:
    token = (request.cookies.get(COOKIE_NAME) or "").strip()
    if len(token) < 32:
        token = _new_token()
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            secure=_cookie_secure(),
            samesite="lax",
            path="/",
        )
    return Owner(token=token, owner_hash=_owner_hash(token))


@router.get("/identity")
def ensure_identity(request: Request, response: Response) -> dict[str, bool]:
    current_owner(request, response)
    return {"ok": True}


def assign_deck_owner(thread_id: str, owner_hash: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO deck_owners (thread_id, owner_hash, created_at)
            VALUES (?, ?, COALESCE(
                (SELECT created_at FROM deck_owners WHERE thread_id = ?),
                ?
            ))
            """,
            (thread_id, owner_hash, thread_id, _now()),
        )
        conn.commit()


def assign_deck_to_owner(thread_id: str, owner: Owner) -> None:
    if isinstance(owner, Owner):
        assign_deck_owner(thread_id, owner.owner_hash)
        return
    if token := os.getenv("OSZ_TEST_OWNER_TOKEN"):
        assign_deck_owner(thread_id, _owner_hash(token))


def inherit_deck_owner(source_thread_id: str, new_thread_id: str) -> None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT owner_hash FROM deck_owners WHERE thread_id = ?",
            (source_thread_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown deck")
        conn.execute(
            """
            INSERT OR REPLACE INTO deck_owners (thread_id, owner_hash, created_at)
            VALUES (?, ?, ?)
            """,
            (new_thread_id, row["owner_hash"], _now()),
        )
        conn.commit()


def require_deck_owner(thread_id: str, owner: Owner) -> None:
    if not isinstance(owner, Owner):
        return
    with _connect() as conn:
        row = conn.execute(
            "SELECT owner_hash FROM deck_owners WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
    if row is None or row["owner_hash"] != owner.owner_hash:
        raise HTTPException(status_code=404, detail="Unknown deck")


def owned_thread_ids(owner: Owner) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT thread_id
            FROM deck_owners
            WHERE owner_hash = ?
            ORDER BY created_at DESC
            """,
            (owner.owner_hash,),
        ).fetchall()
    return [str(row["thread_id"]) for row in rows]


def delete_deck_owner(thread_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM deck_owners WHERE thread_id = ?", (thread_id,))
        conn.commit()


def delete_deck_owners(thread_ids: Iterable[str]) -> None:
    ids = [str(thread_id) for thread_id in thread_ids]
    if not ids:
        return
    with _connect() as conn:
        conn.executemany("DELETE FROM deck_owners WHERE thread_id = ?", [(tid,) for tid in ids])
        conn.commit()
