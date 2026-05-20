"""Visual playground endpoints.

This is an opt-in visual-style decision aid. It is intentionally separate from
the creator playground and from outline-chat advanced mode: only a deck that has
explicitly entered the visual_playground stage after outline approval can
generate temporary sample-slide previews, then commit only the selected visual
style back into graph state.
"""

from __future__ import annotations

import contextvars
import json
import logging
import queue as _queue
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from ..catalog import layouts as layout_catalog
from ..graph.nodes import advanced_chat, html_one
from ..llm import zenmux
from ..llm.models import get_lane_model, get_lane_thinking_effort
from ..llm.runtime_config import (
    RuntimeLLMConfig,
    redact_secrets,
    runtime_config_from_request,
    use_runtime_config,
)
from ..llm.stream import push_event, tagged_stream, writer_override
from . import playground_store
from .common import config_for, current_state, graph, mirror_to_disk
from .history import _clone_checkpoint_lineage
from .owners import Owner, assign_deck_to_owner, current_owner, require_deck_owner
from .streaming import SSE_HEADERS, _sse, _stream_graph

router = APIRouter()
log = logging.getLogger(__name__)

MAX_VISUAL_PLAYGROUND_CANDIDATES = 5
DEFAULT_VISUAL_PLAYGROUND_CANDIDATES = 3
MAX_PREVIEW_SLIDES_PER_CANDIDATE = 2
_SENTINEL = object()


class VisualPlaygroundGenerateBody(BaseModel):
    candidate_count: int = DEFAULT_VISUAL_PLAYGROUND_CANDIDATES
    guidance: str | None = None
    html_critic_enabled: bool = True

    @field_validator("candidate_count")
    @classmethod
    def _candidate_count_in_range(cls, value: int) -> int:
        if value < 1 or value > MAX_VISUAL_PLAYGROUND_CANDIDATES:
            raise ValueError(
                f"candidate_count must be between 1 and {MAX_VISUAL_PLAYGROUND_CANDIDATES}."
            )
        return value


class VisualPlaygroundSelectBody(BaseModel):
    candidate_id: str


class VisualPlaygroundContinueBody(BaseModel):
    destination: Literal["layout", "playground"] = "layout"
    candidate_ids: list[str] = Field(default_factory=list)


class _CandidateSpec(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    rationale: str = Field(default="", max_length=600)
    guidance: str = Field(default="", max_length=1200)
    visual_style: advanced_chat.AdvancedVisualStyle


class _CandidateBatch(BaseModel):
    candidates: list[_CandidateSpec] = Field(min_length=1, max_length=MAX_VISUAL_PLAYGROUND_CANDIDATES)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_waiting_at_visual_playground(values: dict[str, Any]) -> bool:
    return bool(
        values.get("current_stage") == "visual_playground"
        and values.get("outline_slides")
    )


def _require_visual_playground_context(thread_id: str) -> tuple[Literal["outline"], dict[str, Any]]:
    snap = graph().get_state(config_for(thread_id))  # type: ignore[arg-type]
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown deck")
    values = dict(snap.values or {})
    if _is_waiting_at_visual_playground(values):
        return "outline", values
    raise HTTPException(
        status_code=409,
        detail=(
            "Visual Playground is available only after outline approval and before visual style generation."
        ),
    )


def _style_markdown(style: advanced_chat.AdvancedVisualStyle | dict[str, Any]) -> str:
    parsed = (
        style
        if isinstance(style, advanced_chat.AdvancedVisualStyle)
        else advanced_chat.AdvancedVisualStyle.model_validate(style)
    )
    pal = parsed.palette
    return "\n".join([
        "# Visual Style",
        f"Tone: **{parsed.tone}**  |  Density: **{parsed.density}**",
        "",
        "## Palette",
        f"- primary: `{pal.primary}`",
        f"- secondary: `{pal.secondary}`",
        f"- accent: `{pal.accent}`",
        f"- neutral_dark: `{pal.neutral_dark}`",
        f"- neutral_light: `{pal.neutral_light}`",
        f"- background: `{pal.background}`",
        *[f"- {k}: `{v}`" for k, v in (pal.roles or {}).items()],
        "",
        "## Typography",
        f"- heading: {parsed.typography.heading_family}",
        f"- body: {parsed.typography.body_family}",
        f"- display: {parsed.typography.display_family or parsed.typography.heading_family}",
        f"_rationale: {parsed.typography.rationale}_",
        "",
        "## Imagery Policy",
        parsed.imagery_policy,
        "",
        "## Motion",
        parsed.motion_policy,
        "",
        "## Rationale",
        parsed.rationale,
    ])


def _candidate_style(candidate: dict[str, Any]) -> tuple[dict[str, Any], str]:
    try:
        style = advanced_chat.AdvancedVisualStyle.model_validate(candidate.get("visual_style") or {})
    except ValidationError as exc:
        label = candidate.get("label") or candidate.get("candidate_id") or "candidate"
        raise HTTPException(status_code=409, detail=f"Candidate {label} style is invalid: {exc}") from exc
    return style.model_dump(), _style_markdown(style)


def _candidate_ids_from_request(
    values: dict[str, Any],
    body: VisualPlaygroundContinueBody | None,
) -> list[str]:
    requested = list((body.candidate_ids if body else []) or [])
    if not requested:
        stored_ids = values.get("visual_playground_selected_candidate_ids")
        if isinstance(stored_ids, list):
            requested = [str(item) for item in stored_ids if str(item).strip()]
    if not requested and values.get("visual_playground_selected_candidate_id"):
        requested = [str(values["visual_playground_selected_candidate_id"])]

    out: list[str] = []
    seen: set[str] = set()
    for item in requested:
        candidate_id = str(item).strip()
        if candidate_id and candidate_id not in seen:
            out.append(candidate_id)
            seen.add(candidate_id)
    return out


def _resolve_selected_candidates(
    values: dict[str, Any],
    body: VisualPlaygroundContinueBody | None,
) -> list[dict[str, Any]]:
    candidate_ids = _candidate_ids_from_request(values, body)
    candidates = list(values.get("visual_playground_candidates") or [])
    by_id = {str(item.get("candidate_id")): item for item in candidates if item.get("candidate_id")}
    missing = [candidate_id for candidate_id in candidate_ids if candidate_id not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown visual playground candidate: {missing[0]}")
    return [by_id[candidate_id] for candidate_id in candidate_ids]


def _selection_status_update(
    values: dict[str, Any],
    selected_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    selected_ids = [str(candidate["candidate_id"]) for candidate in selected_candidates]
    status = dict(values.get("visual_playground_status") or {})
    return {
        **status,
        "state": "selected",
        "selected_candidate_id": selected_ids[0] if len(selected_ids) == 1 else None,
        "selected_candidate_ids": selected_ids,
        "selected_at": _now(),
    }


def _source_outline(values: dict[str, Any], source: str) -> list[dict[str, Any]]:
    return list(values.get("outline_slides") or [])


def _source_style(values: dict[str, Any], source: str) -> dict[str, Any]:
    return dict(values.get("visual_style") or {})


def _source_style_md(values: dict[str, Any], source: str) -> str:
    return str(values.get("visual_style_md") or "")


def _outline_summary(outline_slides: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for idx, slide in enumerate(outline_slides[:12], 1):
        bullets = slide.get("bullets") or []
        slot_count = len(slide.get("image_slots") or [])
        lines.append(
            f"{idx}. {slide.get('title') or 'Untitled'} "
            f"(role={slide.get('role') or 'content'}, bullets={len(bullets)}, image_slots={slot_count})"
        )
    return "\n".join(lines)


def _candidate_messages(
    values: dict[str, Any],
    source: str,
    *,
    candidate_count: int,
    guidance: str,
) -> list[dict[str, str]]:
    outline_slides = _source_outline(values, source)
    current_style = _source_style(values, source)
    current_style_md = _source_style_md(values, source)
    return [
        {
            "role": "system",
            "content": (
                "You are a senior presentation visual director. Generate distinct visual "
                "style candidates for a slide deck. Return complete style systems only; "
                "do not change the outline, slide count, or layout decisions. The user "
                "will see sample-slide previews, but only the visual_style object can be "
                "selected and locked here."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Generate exactly {candidate_count} candidates.\n\n"
                f"Deck name: {values.get('deck_name') or 'Untitled deck'}\n"
                f"Expected slides: {values.get('expected_pages', len(outline_slides) or 8)}\n"
                f"Aspect ratio: {values.get('aspect_ratio', '16:9')}\n"
                f"Density preference: {values.get('density_preference', 'balanced')}\n"
                f"Language: {values.get('language', 'en')}\n"
                f"Visual preference hint: {values.get('visual_style_preference') or '(none)'}\n"
                f"Visual preset: {values.get('visual_style_preset_label') or '(none)'}\n"
                f"User guidance: {guidance or '(none)'}\n\n"
                f"Current visual style markdown:\n{current_style_md[:4000] or '(none)'}\n\n"
                f"Current visual style JSON:\n{current_style}\n\n"
                f"Outline summary:\n{_outline_summary(outline_slides)}\n\n"
                "Make the candidates visually meaningfully different in tone, palette, "
                "typography, density, and imagery policy while staying appropriate to "
                "the same deck content."
            ),
        },
    ]


def _preview_slide_indices(outline_slides: list[dict[str, Any]]) -> list[int]:
    if not outline_slides:
        return []
    first_idx = 0
    content_idx: int | None = None
    for idx, slide in enumerate(outline_slides):
        role = str(slide.get("role") or "").lower()
        if idx != first_idx and (slide.get("bullets") or slide.get("image_slots")):
            content_idx = idx
            break
        if idx != first_idx and role not in {"cover", "opening", "opener", "intro"}:
            content_idx = idx
            break
    out = [first_idx]
    if content_idx is not None:
        out.append(content_idx)
    elif len(outline_slides) > 1:
        out.append(1)
    return out[:MAX_PREVIEW_SLIDES_PER_CANDIDATE]


def _preview_pattern(slide: dict[str, Any], *, is_first_preview: bool) -> str:
    role = str(slide.get("role") or "").lower()
    image_slots = slide.get("image_slots") or []
    bullets = slide.get("bullets") or []
    if is_first_preview or role in {"cover", "opening", "opener", "intro"}:
        return "editorial_hero_split"
    if len(image_slots) >= 2:
        return "image_gallery_grid"
    if 1 <= len(bullets) <= 4:
        return "editorial_reason_cards"
    return "content_f_shape"


def _brief_slide(outline_slide: dict[str, Any], slide_idx: int, pattern_id: str) -> dict[str, Any]:
    pattern = layout_catalog.PATTERNS[pattern_id]
    return {
        "slide_idx": slide_idx,
        "title": outline_slide.get("title") or f"Slide {slide_idx + 1}",
        "role": outline_slide.get("role") or "content",
        "bullets": list(outline_slide.get("bullets") or []),
        "image_slots": list(outline_slide.get("image_slots") or []),
        "speaker_notes": outline_slide.get("speaker_notes", ""),
        "pattern": pattern_id,
        "family": pattern["family"],
        "zones": pattern["zones"],
        "content_shape": "visual_style_preview",
        "wireframe": "",
    }


def _preview_brief(
    values: dict[str, Any],
    source: str,
    candidate: dict[str, Any],
    outline_slides: list[dict[str, Any]],
    preview_indices: list[int],
    *,
    html_critic_enabled: bool,
) -> dict[str, Any]:
    brief_slides = [
        _brief_slide(
            outline_slides[idx],
            idx,
            _preview_pattern(outline_slides[idx], is_first_preview=preview_idx == 0),
        )
        for preview_idx, idx in enumerate(preview_indices)
        if idx < len(outline_slides)
    ]
    style = candidate["visual_style"]
    return {
        "language": values.get("language", "en"),
        "aspect_ratio": values.get("aspect_ratio", "16:9"),
        "density": style.get("density", values.get("density_preference", "balanced")),
        "style": style,
        "visual_style_preference": (
            f"Visual playground candidate: {candidate.get('label')}. "
            f"{candidate.get('guidance') or candidate.get('rationale') or ''}"
        ).strip(),
        "visual_style_preset_id": values.get("visual_style_preset_id"),
        "visual_style_preset_label": values.get("visual_style_preset_label"),
        "visual_style_preset_prompt": values.get("visual_style_preset_prompt"),
        "visual_style_preset_style_bias": values.get("visual_style_preset_style_bias"),
        "visual_style_preset_layout_bias": values.get("visual_style_preset_layout_bias"),
        "visual_style_preset_html_rules": values.get("visual_style_preset_html_rules"),
        "creator_prompt": "",
        "lane_model_overrides": values.get("lane_model_overrides"),
        "lane_thinking_effort_overrides": values.get("lane_thinking_effort_overrides"),
        "html_critic_enabled": html_critic_enabled,
        "slides": brief_slides,
    }


def _render_preview_slides(
    values: dict[str, Any],
    source: str,
    candidate: dict[str, Any],
    *,
    html_critic_enabled: bool,
) -> list[dict[str, Any]]:
    outline_slides = _source_outline(values, source)
    preview_indices = _preview_slide_indices(outline_slides)
    brief = _preview_brief(
        values,
        source,
        candidate,
        outline_slides,
        preview_indices,
        html_critic_enabled=html_critic_enabled,
    )
    previews: list[dict[str, Any]] = []
    for slide in brief["slides"]:
        slide_idx = int(slide["slide_idx"])
        update = html_one.html_one_node({"slide_idx": slide_idx, "brief": brief})
        html_map = update.get("html_slides") or {}
        failures = update.get("html_failures") or []
        html = html_map.get(slide_idx) or html_map.get(str(slide_idx))
        error = failures[0]["reason"] if failures else None
        previews.append({
            "slide_idx": slide_idx,
            "title": slide.get("title"),
            "pattern": slide.get("pattern"),
            "html": html,
            "error": error,
        })
    return previews


def _persist_visual_playground_state(thread_id: str, update: dict[str, Any]) -> None:
    graph().update_state(config_for(thread_id), update)  # type: ignore[arg-type]
    mirror_to_disk(thread_id)


def _run_visual_playground(
    thread_id: str,
    body: VisualPlaygroundGenerateBody,
    *,
    emit_state_update: Callable[[], None] | None = None,
) -> dict[str, Any]:
    source, values = _require_visual_playground_context(thread_id)
    guidance = (body.guidance or "").strip()
    status = {
        "state": "running",
        "candidate_count": body.candidate_count,
        "guidance": guidance,
        "html_critic_enabled": body.html_critic_enabled,
        "started_at": _now(),
        "warning": (
            "This mode can be very token-consuming. These previews lock only visual style; "
            "final layout and slide composition may change in later stages."
        ),
    }
    _persist_visual_playground_state(
        thread_id,
        {
            "visual_playground_status": status,
            "visual_playground_candidates": [],
            "visual_playground_selected_candidate_id": None,
            "visual_playground_selected_candidate_ids": [],
            "current_stage": "visual_playground",
        },
    )
    if emit_state_update:
        emit_state_update()

    model = get_lane_model(values, "style", "style.text")
    reasoning_effort = get_lane_thinking_effort(values, "style")
    event: dict[str, Any] = {"node": "visual_playground", "state": "started", "model": model}
    if reasoning_effort:
        event["reasoning_effort"] = reasoning_effort
    push_event(event)
    with tagged_stream("visual_playground"):
        batch = zenmux.chat_structured(
            model,
            _candidate_messages(
                values,
                source,
                candidate_count=body.candidate_count,
                guidance=guidance,
            ),
            _CandidateBatch,
            temperature=0.55,
            reasoning_effort=reasoning_effort,
            stream=True,
        )

    raw_candidates = batch.candidates[: body.candidate_count]
    candidates: list[dict[str, Any]] = []
    for idx, spec in enumerate(raw_candidates, 1):
        candidate_id = f"vp-{uuid.uuid4().hex[:8]}"
        payload = {
            "candidate_id": candidate_id,
            "label": spec.label,
            "rationale": spec.rationale,
            "guidance": spec.guidance,
            "visual_style": spec.visual_style.model_dump(),
            "visual_style_md": _style_markdown(spec.visual_style),
            "preview_slides": [],
            "model": model,
            "reasoning_effort": reasoning_effort,
            "created_at": _now(),
            "source": source,
        }
        push_event({
            "node": "visual_playground",
            "state": "candidate_started",
            "candidate_id": candidate_id,
            "candidate_index": idx,
            "candidate_count": len(raw_candidates),
            "label": spec.label,
        })
        try:
            payload["preview_slides"] = _render_preview_slides(
                values,
                source,
                payload,
                html_critic_enabled=body.html_critic_enabled,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("visual playground preview failed")
            payload["error"] = redact_secrets(exc)
            push_event({
                "node": "visual_playground",
                "state": "candidate_error",
                "candidate_id": candidate_id,
                "error": payload["error"],
            })
        candidates.append(payload)
        _persist_visual_playground_state(
            thread_id,
            {
                "visual_playground_status": {**status, "state": "running", "completed_candidates": len(candidates)},
                "visual_playground_candidates": candidates,
                "current_stage": "visual_playground",
            },
        )
        if emit_state_update:
            emit_state_update()
        push_event({
            "node": "visual_playground",
            "state": "candidate_finished",
            "candidate_id": candidate_id,
            "candidate_index": idx,
            "candidate_count": len(raw_candidates),
        })

    final_status = {
        **status,
        "state": "ready",
        "completed_candidates": len(candidates),
        "finished_at": _now(),
    }
    _persist_visual_playground_state(
        thread_id,
        {
            "visual_playground_status": final_status,
            "visual_playground_candidates": candidates,
            "current_stage": "visual_playground",
        },
    )
    if emit_state_update:
        emit_state_update()
    push_event({"node": "visual_playground", "state": "finished", "model": model})
    return {"ok": True, "source": source, "candidate_count": len(candidates)}


def _run_with_streaming(thread_id: str, body: VisualPlaygroundGenerateBody) -> Iterator[str]:
    q: _queue.SimpleQueue[Any] = _queue.SimpleQueue()

    def writer(msg: dict[str, Any]) -> None:
        q.put(msg)

    ctx = contextvars.copy_context()

    def worker() -> None:
        try:
            with writer_override(writer):
                result = _run_visual_playground(
                    thread_id,
                    body,
                    emit_state_update=lambda: q.put({"__state_update": current_state(thread_id)}),
                )
            q.put({"__done": True, "result": result})
        except Exception as exc:  # noqa: BLE001
            log.exception("visual playground stream failed")
            q.put({"__error": redact_secrets(exc)})
        finally:
            q.put(_SENTINEL)

    import threading

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
                "node": "visual_playground",
                "patch": {
                    "visual_playground_status": state.get("values", {}).get("visual_playground_status"),
                    "visual_playground_selected_candidate_id": state.get("values", {}).get(
                        "visual_playground_selected_candidate_id"
                    ),
                    "visual_playground_candidates": state.get("values", {}).get("visual_playground_candidates", []),
                },
            })
            continue
        if "__done" in msg:
            state = current_state(thread_id)
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


@router.post("/decks/{thread_id}/visual_playground/stream")
def visual_playground_stream(
    thread_id: str,
    body: VisualPlaygroundGenerateBody,
    owner: Owner = Depends(current_owner),
    runtime_config: RuntimeLLMConfig = Depends(runtime_config_from_request),
) -> StreamingResponse:
    require_deck_owner(thread_id, owner)
    _require_visual_playground_context(thread_id)

    def gen() -> Iterable[str]:
        with use_runtime_config(runtime_config):
            yield from _run_with_streaming(thread_id, body)

    return StreamingResponse(gen(), headers=SSE_HEADERS)


@router.post("/decks/{thread_id}/visual_playground/select")
def select_visual_playground_candidate(
    thread_id: str,
    body: VisualPlaygroundSelectBody,
    owner: Owner = Depends(current_owner),
) -> dict[str, Any]:
    require_deck_owner(thread_id, owner)
    source, values = _require_visual_playground_context(thread_id)
    candidates = list(values.get("visual_playground_candidates") or [])
    candidate = next(
        (item for item in candidates if item.get("candidate_id") == body.candidate_id),
        None,
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Unknown visual playground candidate.")
    style_payload, style_md = _candidate_style(candidate)

    update: dict[str, Any] = {
        "visual_playground_selected_candidate_id": body.candidate_id,
        "visual_playground_selected_candidate_ids": [body.candidate_id],
        "visual_playground_status": _selection_status_update(values, [candidate]),
    }
    update.update({
        "visual_style": style_payload,
        "visual_style_md": style_md,
        "current_stage": "visual_playground",
    })

    _persist_visual_playground_state(thread_id, update)
    return current_state(thread_id)


def _lane_response(row: dict[str, Any]) -> dict[str, Any]:
    try:
        state = current_state(row["lane_thread_id"])
    except Exception:
        state = None
    return {
        "lane_id": row["lane_id"],
        "lane_thread_id": row["lane_thread_id"],
        "creator_prompt": row["creator_prompt"],
        "lane_name": row["lane_name"],
        "created_at": row["created_at"],
        "state": state,
    }


def _prepare_candidate_lane(
    *,
    thread_id: str,
    source_cfg: dict[str, Any],
    base_values: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    lane_thread_id = uuid.uuid4().hex[:12]
    creator_prompt = ""
    lane_name = str(candidate.get("label") or candidate.get("candidate_id") or "").strip() or None
    style_payload, style_md = _candidate_style(candidate)
    lane = playground_store.create_lane_record(
        thread_id,
        lane_thread_id,
        creator_prompt,
        lane_name=lane_name,
    )
    try:
        new_target_cfg = _clone_checkpoint_lineage(thread_id, source_cfg, lane_thread_id)
        lane_cfg = graph().update_state(  # type: ignore[arg-type]
            new_target_cfg,
            {
                "thread_id": lane_thread_id,
                "deck_name": f"{base_values.get('deck_name') or thread_id} · {lane['lane_name']}",
                "parent_thread_id": thread_id,
                "lane_id": lane["lane_id"],
                "creator_prompt": creator_prompt,
                "lane_model_overrides": None,
                "lane_thinking_effort_overrides": None,
                "visual_style": style_payload,
                "visual_style_md": style_md,
                "current_stage": "layout",
            },
            as_node="await_style",
        )
    except Exception:
        playground_store.delete_lane_record(thread_id, lane["lane_id"])
        raise
    return lane, lane_cfg


def _parse_sse_frame(frame: str) -> dict[str, Any] | None:
    for line in frame.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[len("data:"):].strip()
        if not raw:
            continue
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def _stream_candidate_lane(
    lane: dict[str, Any],
    lane_cfg: dict[str, Any],
) -> Iterator[str]:
    lane_id = str(lane["lane_id"])
    lane_thread_id = str(lane["lane_thread_id"])
    yield _sse({
        "type": "lane_started",
        "lane_id": lane_id,
        "thread_id": lane_thread_id,
        "lane": _lane_response(lane),
    })
    for frame in _stream_graph(None, lane_cfg, lane_thread_id):
        event = _parse_sse_frame(frame)
        if not event:
            continue
        event_type = event.get("type")
        if event_type == "error":
            yield _sse({**event, "lane_id": lane_id, "thread_id": lane_thread_id})
        elif event_type == "done":
            yield _sse({
                "type": "lane_done",
                "lane_id": lane_id,
                "thread_id": lane_thread_id,
                "lane": _lane_response(lane),
            })


@router.post("/decks/{thread_id}/visual_playground/continue/stream")
def continue_visual_playground(
    thread_id: str,
    body: VisualPlaygroundContinueBody | None = None,
    owner: Owner = Depends(current_owner),
    runtime_config: RuntimeLLMConfig = Depends(runtime_config_from_request),
) -> StreamingResponse:
    require_deck_owner(thread_id, owner)
    snap = graph().get_state(config_for(thread_id))  # type: ignore[arg-type]
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown deck")
    values = dict(snap.values or {})
    if values.get("current_stage") != "visual_playground":
        raise HTTPException(status_code=409, detail="Deck is not in visual playground.")
    destination = (body.destination if body else "layout")
    selected_candidates = _resolve_selected_candidates(values, body)
    if destination == "layout" and len(selected_candidates) != 1:
        raise HTTPException(
            status_code=409,
            detail="Continue to layout requires exactly one selected visual playground candidate.",
        )
    if destination == "playground" and not selected_candidates:
        raise HTTPException(
            status_code=409,
            detail="Open Creator Playground requires at least one selected visual playground candidate.",
        )
    selected_ids = [str(candidate["candidate_id"]) for candidate in selected_candidates]
    first_style_payload, first_style_md = _candidate_style(selected_candidates[0])
    selection_update = {
        "visual_playground_selected_candidate_id": selected_ids[0] if len(selected_ids) == 1 else None,
        "visual_playground_selected_candidate_ids": selected_ids,
        "visual_playground_status": _selection_status_update(values, selected_candidates),
        "visual_style": first_style_payload,
        "visual_style_md": first_style_md,
    }
    if destination == "playground":
        existing_lanes = playground_store.list_lanes(thread_id)
        if len(existing_lanes) + len(selected_candidates) > playground_store.MAX_LANES_PER_DECK:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Selected visual styles exceed the remaining Creator Playground lane capacity."
                ),
            )
        prepared_lanes: list[tuple[dict[str, Any], dict[str, Any]]] = []
        try:
            for candidate in selected_candidates:
                lane, lane_cfg = _prepare_candidate_lane(
                    thread_id=thread_id,
                    source_cfg=snap.config,
                    base_values=values,
                    candidate=candidate,
                )
                assign_deck_to_owner(lane["lane_thread_id"], owner)
                prepared_lanes.append((lane, lane_cfg))
        except Exception:
            for lane, _lane_cfg in prepared_lanes:
                playground_store.delete_lane_record(thread_id, lane["lane_id"])
            raise
        graph().update_state(  # type: ignore[arg-type]
            snap.config,
            {**selection_update, "current_stage": "playground"},
            as_node="await_style",
        )
        mirror_to_disk(thread_id)

        def gen_playground() -> Iterable[str]:
            yield _sse({"type": "thread", "thread_id": thread_id})
            with use_runtime_config(runtime_config):
                for lane, lane_cfg in prepared_lanes:
                    yield from _stream_candidate_lane(lane, lane_cfg)
            yield _sse({"type": "done", "state": current_state(thread_id)})

        return StreamingResponse(gen_playground(), headers=SSE_HEADERS)

    lane_cfg = graph().update_state(  # type: ignore[arg-type]
        snap.config,
        {**selection_update, "current_stage": "layout"},
        as_node="await_style",
    )

    def gen() -> Iterable[str]:
        with use_runtime_config(runtime_config):
            yield _sse({"type": "thread", "thread_id": thread_id})
            yield from _stream_graph(None, lane_cfg, thread_id)

    return StreamingResponse(gen(), headers=SSE_HEADERS)
