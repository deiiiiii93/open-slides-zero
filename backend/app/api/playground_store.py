"""SQLite helpers for creator playground metadata.

LangGraph checkpoints remain the source of truth for lane state. These tables
only track parent/lane relationships and saved prompt snippets.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from ..graph import graph as graph_module

MAX_LANES_PER_DECK = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    graph_module.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(graph_module.DB_PATH))
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS playground_lanes (
            parent_thread_id TEXT NOT NULL,
            lane_id TEXT NOT NULL,
            lane_thread_id TEXT NOT NULL UNIQUE,
            creator_prompt TEXT NOT NULL,
            lane_name TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (parent_thread_id, lane_id)
        )
        """
    )
    try:
        conn.execute("ALTER TABLE playground_lanes ADD COLUMN lane_name TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_playground_lanes_parent
        ON playground_lanes(parent_thread_id, created_at)
        """
    )
    try:
        conn.execute("ALTER TABLE playground_lanes DROP COLUMN cutoff")
    except sqlite3.OperationalError:
        # Column already absent (fresh DB) or older SQLite without DROP COLUMN.
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS masterpieces (
            id TEXT PRIMARY KEY,
            owner_hash TEXT,
            prompt TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    try:
        conn.execute("ALTER TABLE masterpieces ADD COLUMN owner_hash TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_masterpieces_owner
        ON masterpieces(owner_hash, created_at)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_masterpieces_owner_prompt
        ON masterpieces(owner_hash, prompt)
        """
    )
    conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    lane_id = str(row["lane_id"])
    creator_prompt = str(row["creator_prompt"])
    lane_name = row["lane_name"]
    return {
        "parent_thread_id": row["parent_thread_id"],
        "lane_id": lane_id,
        "lane_thread_id": row["lane_thread_id"],
        "creator_prompt": creator_prompt,
        "lane_name": str(lane_name).strip() if lane_name else default_lane_name(lane_id, creator_prompt),
        "created_at": row["created_at"],
    }


def _lane_id_label(lane_id: str) -> str:
    return lane_id.replace("lane-", "Lane ")


def default_lane_name(lane_id: str, creator_prompt: str) -> str:
    first_line = next((line.strip() for line in creator_prompt.splitlines() if line.strip()), "")
    collapsed = " ".join(first_line.split())
    if not collapsed:
        return _lane_id_label(lane_id)
    if len(collapsed) <= 36:
        return collapsed
    return f"{collapsed[:33].rstrip()}..."


def list_lanes(parent_thread_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT parent_thread_id, lane_id, lane_thread_id, creator_prompt, lane_name, created_at
            FROM playground_lanes
            WHERE parent_thread_id = ?
            ORDER BY created_at, lane_id
            """,
            (parent_thread_id,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def create_lane_record(
    parent_thread_id: str,
    lane_thread_id: str,
    creator_prompt: str,
) -> dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT lane_id FROM playground_lanes WHERE parent_thread_id = ?",
            (parent_thread_id,),
        ).fetchall()
        if len(rows) >= MAX_LANES_PER_DECK:
            raise ValueError(f"Creator playground allows at most {MAX_LANES_PER_DECK} lanes.")
        used_lane_ids = {str(row["lane_id"]) for row in rows}
        lane_id = next(
            (
                f"lane-{idx}"
                for idx in range(1, MAX_LANES_PER_DECK + 1)
                if f"lane-{idx}" not in used_lane_ids
            ),
            None,
        )
        if lane_id is None:
            raise ValueError(f"Creator playground allows at most {MAX_LANES_PER_DECK} lanes.")
        created_at = _now()
        conn.execute(
            """
            INSERT INTO playground_lanes
            (parent_thread_id, lane_id, lane_thread_id, creator_prompt, lane_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                parent_thread_id,
                lane_id,
                lane_thread_id,
                creator_prompt,
                default_lane_name(lane_id, creator_prompt),
                created_at,
            ),
        )
        conn.commit()
    return {
        "parent_thread_id": parent_thread_id,
        "lane_id": lane_id,
        "lane_thread_id": lane_thread_id,
        "creator_prompt": creator_prompt,
        "lane_name": default_lane_name(lane_id, creator_prompt),
        "created_at": created_at,
    }


def delete_lane_record(parent_thread_id: str, lane_id: str) -> dict[str, Any] | None:
    """Atomically read-and-delete the lane row. Returns the row if it existed,
    None if a concurrent caller deleted it first."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                """
                SELECT parent_thread_id, lane_id, lane_thread_id, creator_prompt, lane_name, created_at
                FROM playground_lanes
                WHERE parent_thread_id = ? AND lane_id = ?
                """,
                (parent_thread_id, lane_id),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                "DELETE FROM playground_lanes WHERE parent_thread_id = ? AND lane_id = ?",
                (parent_thread_id, lane_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return _row_to_dict(row)


def delete_lanes_for_parent(parent_thread_id: str) -> list[dict[str, Any]]:
    rows = list_lanes(parent_thread_id)
    if not rows:
        return []
    with _connect() as conn:
        conn.execute(
            "DELETE FROM playground_lanes WHERE parent_thread_id = ?",
            (parent_thread_id,),
        )
        conn.commit()
    return rows


def get_lane(parent_thread_id: str, lane_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT parent_thread_id, lane_id, lane_thread_id, creator_prompt, lane_name, created_at
            FROM playground_lanes
            WHERE parent_thread_id = ? AND lane_id = ?
            """,
            (parent_thread_id, lane_id),
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_lane_by_thread(lane_thread_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT parent_thread_id, lane_id, lane_thread_id, creator_prompt, lane_name, created_at
            FROM playground_lanes
            WHERE lane_thread_id = ?
            """,
            (lane_thread_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def delete_lane_for_thread(lane_thread_id: str) -> dict[str, Any] | None:
    row = get_lane_by_thread(lane_thread_id)
    if row is None:
        return None
    with _connect() as conn:
        conn.execute(
            "DELETE FROM playground_lanes WHERE lane_thread_id = ?",
            (lane_thread_id,),
        )
        conn.commit()
    return row


def update_lane_name(parent_thread_id: str, lane_id: str, lane_name: str) -> dict[str, Any] | None:
    clean_name = " ".join(lane_name.strip().split())
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT parent_thread_id, lane_id, lane_thread_id, creator_prompt, lane_name, created_at
            FROM playground_lanes
            WHERE parent_thread_id = ? AND lane_id = ?
            """,
            (parent_thread_id, lane_id),
        ).fetchone()
        if row is None:
            return None
        stored_name = clean_name[:80] if clean_name else None
        conn.execute(
            """
            UPDATE playground_lanes
            SET lane_name = ?
            WHERE parent_thread_id = ? AND lane_id = ?
            """,
            (stored_name, parent_thread_id, lane_id),
        )
        conn.commit()
        updated = conn.execute(
            """
            SELECT parent_thread_id, lane_id, lane_thread_id, creator_prompt, lane_name, created_at
            FROM playground_lanes
            WHERE parent_thread_id = ? AND lane_id = ?
            """,
            (parent_thread_id, lane_id),
        ).fetchone()
    return _row_to_dict(updated) if updated else None


def _masterpiece_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "prompt": row["prompt"],
        "created_at": row["created_at"],
    }


def list_masterpieces(owner_hash: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, prompt, created_at
            FROM masterpieces
            WHERE owner_hash = ?
            ORDER BY created_at DESC
            """,
            (owner_hash,),
        ).fetchall()
    return [_masterpiece_to_dict(row) for row in rows]


def save_masterpiece(owner_hash: str, prompt: str) -> dict[str, Any]:
    clean_prompt = prompt.strip()
    if not clean_prompt:
        raise ValueError("Cannot save a blank masterpiece prompt.")

    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT id, prompt, created_at
            FROM masterpieces
            WHERE owner_hash = ? AND prompt = ?
            """,
            (owner_hash, clean_prompt),
        ).fetchone()
        if existing:
            return _masterpiece_to_dict(existing)
        masterpiece_id = uuid.uuid4().hex[:12]
        created_at = _now()
        conn.execute(
            """
            INSERT INTO masterpieces (id, owner_hash, prompt, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (masterpiece_id, owner_hash, clean_prompt, created_at),
        )
        conn.commit()
    return {"id": masterpiece_id, "prompt": clean_prompt, "created_at": created_at}


def delete_masterpiece(owner_hash: str, masterpiece_id: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM masterpieces WHERE owner_hash = ? AND id = ?",
            (owner_hash, masterpiece_id),
        )
        conn.commit()
        return cursor.rowcount > 0
