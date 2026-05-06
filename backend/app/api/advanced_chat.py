"""Streaming advanced-mode planning chat.

This endpoint is intentionally outside the LangGraph run. The graph is paused at
the advanced_chat interrupt while each chat turn updates transcript + draft
state, then normal /resume commits the draft into the layout stage.
"""

from __future__ import annotations

import contextvars
import logging
import queue as _queue
import threading
from typing import Any, Callable, Iterable, Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..graph.nodes import advanced_chat as advanced_chat_node
from ..llm import zenmux
from ..llm.models import get_model
from ..llm.runtime_config import (
    RuntimeLLMConfig,
    redact_secrets,
    runtime_config_from_request,
    use_runtime_config,
)
from ..llm.stream import push_event, tagged_stream, writer_override
from .common import config_for, current_state, graph, mirror_to_disk
from .owners import Owner, current_owner, require_deck_owner
from .streaming import SSE_HEADERS, _safe_patch, _sse

router = APIRouter()
log = logging.getLogger(__name__)


class AdvancedChatTurn(BaseModel):
    message: str


_SENTINEL = object()


def _is_advanced_chat_interrupt(snap: Any) -> bool:
    return any(
        (item.value if hasattr(item, "value") else item).get("gate") == "advanced_chat"
        for item in (getattr(snap, "interrupts", None) or [])
        if isinstance(item.value if hasattr(item, "value") else item, dict)
    )


def _waiting_at_advanced_chat(snap: Any) -> bool:
    values = snap.values if snap and snap.values else {}
    return bool(
        values.get("agent_mode") == "advanced"
        and (
            values.get("current_stage") == "advanced_chat"
            or _is_advanced_chat_interrupt(snap)
            or list(getattr(snap, "next", ()) or ()) == ["await_advanced_chat"]
        )
    )


def _require_advanced_chat_state(thread_id: str) -> dict[str, Any]:
    snap = graph().get_state(config_for(thread_id))  # type: ignore[arg-type]
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown deck")
    values = snap.values
    if values.get("agent_mode") != "advanced":
        raise HTTPException(status_code=409, detail="Deck is not in advanced mode.")
    if not _waiting_at_advanced_chat(snap):
        raise HTTPException(status_code=409, detail="Deck is not waiting at advanced chat.")
    if values.get("current_stage") == "advanced_chat":
        return values
    return {**values, "current_stage": "advanced_chat"}


def _persist_chat_state(
    thread_id: str,
    *,
    messages: list[dict[str, Any]],
    draft: dict[str, Any],
) -> dict[str, Any] | None:
    snap = graph().get_state(config_for(thread_id))  # type: ignore[arg-type]
    if not _waiting_at_advanced_chat(snap):
        return None
    update = {
        "advanced_chat_messages": messages,
        "advanced_chat_draft": draft,
        "current_stage": "advanced_chat",
    }
    graph().update_state(config_for(thread_id), update)  # type: ignore[arg-type]
    mirror_to_disk(thread_id)
    return update


def _run_chat_turn(
    thread_id: str,
    message: str,
    *,
    emit_state_update: Callable[[], None] | None = None,
) -> dict[str, Any]:
    values = _require_advanced_chat_state(thread_id)
    text = message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    current_draft = values.get("advanced_chat_draft") or advanced_chat_node.empty_advanced_chat_draft(values)
    messages = [
        *list(values.get("advanced_chat_messages") or []),
        {"role": "user", "content": text},
    ]
    if _persist_chat_state(thread_id, messages=messages, draft=current_draft) is None:
        return {"ok": False, "aborted": True, "reason": "advanced chat is no longer active"}
    if emit_state_update:
        emit_state_update()

    model = get_model("advanced_chat")
    push_event({"node": "advanced_chat", "state": "started", "model": model})
    with tagged_stream("advanced_chat"):
        assistant = zenmux.chat(
            model,
            advanced_chat_node.conversation_messages(
                values,
                messages,
                include_materials=True,
            ),
            temperature=0.45,
            stream=True,
    )

    full_messages = [*messages, {"role": "assistant", "content": assistant}]
    text_choices = advanced_chat_node.choices_from_text(assistant)
    full_messages[-1]["choices"] = text_choices or advanced_chat_node.suggested_choices(current_draft)
    if _persist_chat_state(thread_id, messages=full_messages, draft=current_draft) is None:
        return {"ok": False, "aborted": True, "reason": "advanced chat is no longer active"}
    if emit_state_update:
        emit_state_update()

    try:
        latest_values = _require_advanced_chat_state(thread_id)
        draft = zenmux.chat_structured(
            model,
            advanced_chat_node.draft_extraction_messages(latest_values, full_messages),
            advanced_chat_node.AdvancedChatDraft,
            temperature=0.2,
            stream=False,
            timeout=30,
        )
        draft_payload = advanced_chat_node.advanced_draft_payload(draft)
    except Exception as exc:  # noqa: BLE001
        log.exception("advanced chat draft extraction failed")
        draft_payload = current_draft
        push_event({
            "node": "advanced_chat",
            "state": "error",
            "model": model,
            "error": f"draft extraction failed: {exc}",
        })
    full_messages[-1]["choices"] = text_choices or advanced_chat_node.suggested_choices(draft_payload)
    if _persist_chat_state(
        thread_id,
        messages=full_messages,
        draft=draft_payload,
    ) is None:
        return {"ok": False, "aborted": True, "reason": "advanced chat is no longer active"}
    if emit_state_update:
        emit_state_update()
    push_event({"node": "advanced_chat", "state": "finished", "model": model})
    return {"ok": True, "message_count": len(full_messages), "draft": draft_payload}


def _run_with_streaming(thread_id: str, message: str) -> Iterator[str]:
    q: _queue.SimpleQueue[Any] = _queue.SimpleQueue()

    def writer(msg: dict[str, Any]) -> None:
        q.put(msg)

    ctx = contextvars.copy_context()

    def worker() -> None:
        try:
            with writer_override(writer):
                result = _run_chat_turn(
                    thread_id,
                    message,
                    emit_state_update=lambda: q.put({"__state_update": current_state(thread_id)}),
                )
            q.put({"__done": True, "result": result})
        except Exception as exc:  # noqa: BLE001
            log.exception("advanced chat stream failed")
            q.put({"__error": redact_secrets(exc)})
        finally:
            q.put(_SENTINEL)

    t = threading.Thread(target=ctx.run, args=(worker,), daemon=True)
    t.start()

    yield _sse({"type": "thread", "thread_id": thread_id})
    while True:
        msg = q.get()
        if msg is _SENTINEL:
            break
        if "__error" in msg:
            yield _sse({"type": "error", "message": msg["__error"]})
            continue
        if "__state_update" in msg:
            state = msg["__state_update"]
            yield _sse({
                "type": "update",
                "node": "advanced_chat",
                "patch": _safe_patch(state.get("values", {})),
            })
            continue
        if "__done" in msg:
            state = current_state(thread_id)
            yield _sse({
                "type": "update",
                "node": "advanced_chat",
                "patch": _safe_patch(state.get("values", {})),
            })
            yield _sse({"type": "done", "result": msg["result"], "state": state})
            continue
        ch = msg.get("channel")
        if ch == "tokens":
            yield _sse({
                "type": "token",
                "tag": msg.get("tag"),
                "text": msg.get("text", ""),
            })
        elif ch == "event":
            ev = {k: v for k, v in msg.items() if k != "channel"}
            ev["type"] = "event"
            yield _sse(ev)

    t.join(timeout=2)


@router.post("/decks/{thread_id}/advanced_chat/stream")
def advanced_chat_stream(
    thread_id: str,
    body: AdvancedChatTurn,
    owner: Owner = Depends(current_owner),
    runtime_config: RuntimeLLMConfig = Depends(runtime_config_from_request),
) -> StreamingResponse:
    require_deck_owner(thread_id, owner)
    _require_advanced_chat_state(thread_id)

    def gen() -> Iterable[str]:
        with use_runtime_config(runtime_config):
            yield from _run_with_streaming(thread_id, body.message)

    return StreamingResponse(gen(), headers=SSE_HEADERS)
