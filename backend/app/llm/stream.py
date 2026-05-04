"""Stream adapter: pass raw-OpenAI token chunks into LangGraph's stream OR
into any caller-supplied writer via `writer_override`.

There are two situations where we want tokens to flow:

1. Inside a LangGraph node running under `graph.stream(stream_mode=[..., "custom"])`.
   `get_stream_writer()` returns a real writer; `push_token()` forwards.

2. Inside a FastAPI SSE endpoint that calls a node function directly (edit-loop
   apply_edits path). There is no graph running, so `get_stream_writer()` raises.
   The endpoint enters `with writer_override(my_writer):` to capture the tokens.
"""

from __future__ import annotations

import contextvars
import threading
from typing import Any, Callable

try:
    from langgraph.config import get_stream_writer  # type: ignore
except Exception:  # pragma: no cover
    def get_stream_writer() -> Any | None:  # type: ignore[misc]
        return None


# Best-effort cancellation: SSE endpoints register a per-thread event before
# starting graph.stream(); DELETE endpoints set the event to signal cancellation.
# The streaming loop checks the event between chunks. The upstream LLM HTTP
# request still completes in the background — full kill would require plumbing
# httpx cancel() through zenmux.chat.
_lane_cancel_events: dict[str, threading.Event] = {}
_lane_cancel_lock = threading.Lock()


class LaneCancelled(RuntimeError):
    pass


def register_cancel_event(thread_id: str) -> threading.Event:
    event = threading.Event()
    with _lane_cancel_lock:
        _lane_cancel_events[thread_id] = event
    return event


def unregister_cancel_event(thread_id: str) -> None:
    with _lane_cancel_lock:
        _lane_cancel_events.pop(thread_id, None)


def cancel_lane(thread_id: str) -> bool:
    with _lane_cancel_lock:
        event = _lane_cancel_events.get(thread_id)
    if event is None:
        return False
    event.set()
    return True


def is_lane_cancelled(thread_id: str) -> bool:
    with _lane_cancel_lock:
        event = _lane_cancel_events.get(thread_id)
    return event is not None and event.is_set()


# Tag identifies which subagent is emitting (outline / style / layout / html:N).
_current_tag: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "osz_stream_tag", default=None,
)

# Endpoint-supplied writer takes precedence over LangGraph's stream writer when
# set. Used by SSE endpoints that call node functions directly.
_override_writer: contextvars.ContextVar[Callable[[dict[str, Any]], None] | None] = (
    contextvars.ContextVar("osz_override_writer", default=None)
)


def set_tag(tag: str | None) -> contextvars.Token:
    return _current_tag.set(tag)


def reset_tag(token: contextvars.Token) -> None:
    _current_tag.reset(token)


class tagged_stream:
    """`with tagged_stream("outline"): chat(...)` — tags tokens for the duration."""
    def __init__(self, tag: str) -> None:
        self._tag = tag
        self._cv_token: contextvars.Token | None = None

    def __enter__(self) -> "tagged_stream":
        self._cv_token = set_tag(self._tag)
        return self

    def __exit__(self, *_: Any) -> None:
        if self._cv_token is not None:
            reset_tag(self._cv_token)


class writer_override:
    """`with writer_override(my_writer): ...` — routes push_token/push_event
    to the supplied callable instead of LangGraph's stream writer.

    The callable receives a dict with {channel, ...}. SSE endpoints translate
    these into SSE frames; tests can use it to collect emitted tokens.
    """
    def __init__(self, writer: Callable[[dict[str, Any]], None]) -> None:
        self._writer = writer
        self._cv_token: contextvars.Token | None = None

    def __enter__(self) -> "writer_override":
        self._cv_token = _override_writer.set(self._writer)
        return self

    def __exit__(self, *_: Any) -> None:
        if self._cv_token is not None:
            _override_writer.reset(self._cv_token)


def _safe_writer() -> Callable[[dict[str, Any]], None] | None:
    """Return the active writer, if any.

    Priority:
      1. Explicit `writer_override` set by the caller
      2. LangGraph's `get_stream_writer()` when inside a node of graph.stream()
      3. None (no-op)
    """
    override = _override_writer.get()
    if override is not None:
        return override
    try:
        return get_stream_writer()
    except Exception:
        # langgraph raises RuntimeError when called outside a runnable context
        return None


def push_token(text: str, *, tag: str | None = None, channel: str = "tokens") -> None:
    writer = _safe_writer()
    if writer is None:
        return
    resolved_tag = tag or _current_tag.get()
    try:
        writer({"channel": channel, "tag": resolved_tag, "text": text})
    except Exception:
        pass


def push_event(payload: dict[str, Any]) -> None:
    writer = _safe_writer()
    if writer is None:
        return
    try:
        writer({"channel": "event", **payload})
    except Exception:
        pass
