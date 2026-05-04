"""Slide comment intake + edit-loop endpoints.

Routes:
  POST /decks/{id}/slides/{n}/comments             — persist a comment (sync)
  POST /decks/{id}/apply_edits                      — classify + regen (sync)
  POST /decks/{id}/slides/{n}/comments/stream       — combined SSE: add + apply
  POST /decks/{id}/apply_edits/stream               — SSE-only apply

The streaming variants run the (blocking) LLM work in a worker thread and
bridge `push_token`/`push_event` output into the HTTP response via a queue.

HTML-only edits bypass the graph entirely and call `html_one_node` directly —
so every affected slide re-runs but no other slide is touched. Comments on a
slide are fed to the prompt as `feedback`.
"""

from __future__ import annotations

import contextvars
import logging
import queue as _queue
import threading
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..artifacts import store
from ..graph.nodes.edit import collapse_edit_ops, edit_intent_node
from ..graph.nodes.html_one import html_one_node
from ..graph.nodes.image_insert import apply_image_mappings
from ..llm.stream import tagged_stream, writer_override
from .common import config_for, current_state, graph, mirror_to_disk
from .history import _regenerate_from
from .streaming import SSE_HEADERS, _sse

router = APIRouter()
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CommentIn(BaseModel):
    text: str
    box: dict[str, float] | None = None


# ---------------------------------------------------------------------------
# Internal — targeted html-only regen (called by both sync + SSE variants)
# ---------------------------------------------------------------------------


def _persist_comment(thread_id: str, slide_idx: int, body: CommentIn) -> dict[str, Any]:
    snap = graph().get_state(config_for(thread_id))  # type: ignore[arg-type]
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown deck")
    if snap.values.get("current_stage") != "ready" or snap.interrupts:
        raise HTTPException(
            status_code=409,
            detail="Comments can only be applied after the deck is ready.",
        )
    now = datetime.now(timezone.utc).isoformat()
    comment = {
        "slide_idx": slide_idx,
        "text": body.text,
        "box": body.box,
        "resolved": False,
        "created_at": now,
    }
    graph().update_state(  # type: ignore[arg-type]
        config_for(thread_id),
        {"comments": [comment], "current_stage": "ready"},
        as_node="post_html",
    )
    store.append_comment(thread_id, comment)
    return comment


def _regenerate_html_only(
    thread_id: str,
    affected_slides: list[int],
    comments_by_slide: dict[int, list[str]],
) -> dict[str, Any]:
    """Call html_one_node directly for each affected slide. Assumes any active
    writer_override is already in scope so push_token reaches the caller.
    """
    cfg = config_for(thread_id)
    g = graph()
    snap = g.get_state(cfg)  # type: ignore[arg-type]
    if not snap:
        raise HTTPException(status_code=404, detail="Unknown deck")
    brief = snap.values.get("brief")
    if not brief or not brief.get("slides"):
        return _regenerate_from(
            thread_id, "consolidate", patch=None, affected_slides=affected_slides
        )

    brief_by_idx = {s["slide_idx"]: s for s in brief["slides"]}
    updates: dict[int, str] = {}
    errors: list[str] = []
    for idx in affected_slides:
        if idx not in brief_by_idx:
            errors.append(f"slide {idx}: not in brief — skipping")
            continue
        feedback = "\n".join(comments_by_slide.get(idx, [])) or None
        with tagged_stream(f"html:{idx}"):
            try:
                result = html_one_node({
                    "slide_idx": idx,
                    "brief": brief,
                    "feedback": feedback,
                })
            except Exception as e:  # noqa: BLE001
                log.exception("html_one direct call failed for slide %d", idx)
                errors.append(f"slide {idx}: {e}")
                continue
        for k, v in (result.get("html_slides") or {}).items():
            updates[int(k)] = v
        for e in (result.get("errors") or []):
            errors.append(e)

    if updates:
        update: dict[str, Any] = {
            "html_slides": updates,
            "current_stage": "ready",
            "html_failures": [],
            "pending_html_retry_slides": [],
        }
        plan = snap.values.get("image_insertion_plan") or {}
        applied_mappings = plan.get("applied_mappings") or []
        if snap.values.get("image_insertion_status") == "applied" and applied_mappings:
            base_html = {
                int(k): v
                for k, v in (
                    snap.values.get("html_slides_base")
                    or snap.values.get("html_slides")
                    or {}
                ).items()
            }
            base_html.update(updates)
            image_update = apply_image_mappings(
                {**snap.values, "html_slides_base": base_html},
                applied_mappings,
            )
            update.update(image_update)
        g.update_state(  # type: ignore[arg-type]
            cfg,
            update,
            as_node="post_html",
        )
    mirror_to_disk(thread_id)
    return {
        "ok": True,
        "from_stage": "html",
        "targeted_slides": affected_slides,
        "regenerated_slides": sorted(updates.keys()),
        "state": current_state(thread_id),
        "errors": errors,
    }


def _comments_should_stay_html_only(values: dict[str, Any]) -> bool:
    """Comment edits on non-linear deck modes should not rewind earlier stages."""
    return bool(
        values.get("agent_mode") == "advanced"
        or values.get("parent_thread_id")
        or values.get("lane_id")
    )


def _run_apply_edits(thread_id: str) -> dict[str, Any]:
    snap = graph().get_state(config_for(thread_id))  # type: ignore[arg-type]
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown deck")
    if snap.values.get("current_stage") != "ready" or snap.interrupts:
        raise HTTPException(
            status_code=409,
            detail="Edits can only be applied after the deck is ready.",
        )
    open_comments = [
        c for c in snap.values.get("comments", []) if not c.get("resolved")
    ]
    if not open_comments:
        return {"ok": True, "no_pending": True}

    result = edit_intent_node(snap.values)
    ops = result.get("pending_edit_ops", [])
    if not ops:
        return {"ok": True, "ops": []}

    earliest, patch, affected = collapse_edit_ops(ops)

    comments_by_slide: dict[int, list[str]] = {}
    for c in open_comments:
        idx = int(c.get("slide_idx", 0))
        comments_by_slide.setdefault(idx, []).append(c.get("text", ""))

    comment_slide_ids = sorted({
        int(c.get("slide_idx", 0)) for c in open_comments
    })
    if not affected:
        affected = comment_slide_ids

    if _comments_should_stay_html_only(snap.values) and earliest not in ("html", "image_only") and affected:
        regen_result = _regenerate_html_only(thread_id, affected, comments_by_slide)
        coerced_stage = "html"
    elif earliest in ("html", "image_only") and affected:
        regen_result = _regenerate_html_only(thread_id, affected, comments_by_slide)
        coerced_stage = None
    else:
        regen_result = _regenerate_from(
            thread_id, earliest, patch, affected_slides=affected
        )
        coerced_stage = None
    return {
        "ok": True,
        "earliest_stage": earliest,
        "coerced_stage": coerced_stage,
        "merged_patch": patch,
        "affected_slides": affected,
        "ops": ops,
        "regen": regen_result,
    }


# ---------------------------------------------------------------------------
# Sync endpoints (kept for testing / curl; frontend uses streaming variants)
# ---------------------------------------------------------------------------


@router.post("/decks/{thread_id}/slides/{slide_idx}/comments")
def add_comment(thread_id: str, slide_idx: int, body: CommentIn) -> dict[str, Any]:
    comment = _persist_comment(thread_id, slide_idx, body)
    return {"ok": True, "comment": comment}


@router.post("/decks/{thread_id}/apply_edits")
def apply_edits(thread_id: str) -> dict[str, Any]:
    return _run_apply_edits(thread_id)


# ---------------------------------------------------------------------------
# SSE: bridge push_token/push_event from a worker thread to an HTTP response
# ---------------------------------------------------------------------------


_SENTINEL = object()


def _run_with_streaming(
    thread_id: str,
    work: Any,
) -> Iterator[str]:
    """Run `work(thread_id)` in a worker thread with writer_override installed.
    Yields SSE frames from the captured push_token / push_event calls plus a
    final `done` frame with the state.
    """
    q: _queue.SimpleQueue[Any] = _queue.SimpleQueue()

    def writer(msg: dict[str, Any]) -> None:
        q.put(msg)

    # Capture the parent's contextvars (e.g. any surrounding tagged_stream).
    ctx = contextvars.copy_context()

    def worker() -> None:
        try:
            with writer_override(writer):
                result = work(thread_id)
            q.put({"__done": True, "result": result})
        except Exception as e:  # noqa: BLE001
            log.exception("streaming worker failed")
            q.put({"__error": str(e)})
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
        if "__done" in msg:
            result = msg["result"]
            yield _sse({"type": "done", "result": result, "state": current_state(thread_id)})
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
        else:
            # Pass-through: unknown channel — send as a generic event for debugging
            yield _sse({"type": "event", **{k: v for k, v in msg.items() if k != "channel"}})

    t.join(timeout=2)


@router.post("/decks/{thread_id}/apply_edits/stream")
def apply_edits_stream(thread_id: str) -> StreamingResponse:
    def gen() -> Iterable[str]:
        yield from _run_with_streaming(thread_id, _run_apply_edits)
    return StreamingResponse(gen(), headers=SSE_HEADERS)


@router.post("/decks/{thread_id}/slides/{slide_idx}/comments/stream")
def add_comment_then_apply_stream(
    thread_id: str,
    slide_idx: int,
    body: CommentIn,
) -> StreamingResponse:
    """Combined endpoint: persist the comment, then stream the edit-apply work.
    Saves the frontend from having to do two round-trips and avoids a race
    where apply_edits could fire before the comment is persisted.
    """
    _persist_comment(thread_id, slide_idx, body)

    def gen() -> Iterable[str]:
        yield _sse({
            "type": "comment_saved",
            "slide_idx": slide_idx,
            "text": body.text,
        })
        yield from _run_with_streaming(thread_id, _run_apply_edits)
    return StreamingResponse(gen(), headers=SSE_HEADERS)
