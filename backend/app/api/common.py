"""Shared FastAPI helpers: graph accessor, state serializers, config builders."""

from __future__ import annotations

import logging
import shutil
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from ..artifacts import store
from ..graph.graph import get_graph

log = logging.getLogger(__name__)


def config_for(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def graph() -> CompiledStateGraph:
    return get_graph()


def delete_thread(thread_id: str) -> None:
    """Drop a thread's checkpoints + on-disk mirror. Safe no-op if absent.

    Used by the create endpoints when a hard failure (e.g. ingest ValueError)
    happens before any successful checkpoint, to prevent zombie threads from
    appearing in GET /decks. Do not call from resume paths — a mid-pipeline
    failure there is recoverable via history rewind.
    """
    try:
        checkpointer = cast(BaseCheckpointSaver, graph().checkpointer)
        checkpointer.delete_thread(thread_id)
    except Exception:
        log.exception("failed to delete checkpoint rows for %s", thread_id)
    artifact_dir = store.ROOT / thread_id
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir, ignore_errors=True)


def current_state(thread_id: str, *, source_thread_id: str | None = None) -> dict[str, Any]:
    g = graph()
    snap = g.get_state(config_for(thread_id))
    state = {
        "thread_id": thread_id,
        "checkpoint_id": snap.config["configurable"].get("checkpoint_id"),
        "values": snap.values,
        "next": list(snap.next),
        "interrupts": [i.value if hasattr(i, "value") else i for i in (snap.interrupts or [])],
        "created_at": getattr(snap, "created_at", None),
    }
    if source_thread_id is not None:
        state["source_thread_id"] = source_thread_id
    return state


def mirror_to_disk(thread_id: str) -> None:
    snap = graph().get_state(config_for(thread_id))
    if snap and snap.values:
        store.write_slide_mirrors(thread_id, snap.values)


__all__ = ["config_for", "graph", "current_state", "delete_thread", "mirror_to_disk"]
