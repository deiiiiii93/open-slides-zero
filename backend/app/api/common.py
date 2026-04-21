"""Shared FastAPI helpers: graph accessor, state serializers, config builders."""

from __future__ import annotations

from typing import Any

from langgraph.graph.state import CompiledStateGraph

from ..artifacts import store
from ..graph.graph import get_graph


def config_for(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def graph() -> CompiledStateGraph:
    return get_graph()


def current_state(thread_id: str) -> dict[str, Any]:
    g = graph()
    snap = g.get_state(config_for(thread_id))
    return {
        "thread_id": thread_id,
        "checkpoint_id": snap.config["configurable"].get("checkpoint_id"),
        "values": snap.values,
        "next": list(snap.next),
        "interrupts": [i.value if hasattr(i, "value") else i for i in (snap.interrupts or [])],
        "created_at": getattr(snap, "created_at", None),
    }


def mirror_to_disk(thread_id: str) -> None:
    snap = graph().get_state(config_for(thread_id))
    if snap and snap.values:
        store.write_slide_mirrors(thread_id, snap.values)


__all__ = ["config_for", "graph", "current_state", "mirror_to_disk"]
