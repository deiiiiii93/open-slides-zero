"""ZenMux-routed LLM adapter — the single concentrated cost of the split-stack.

Provides a `chat()` helper wrapping the OpenAI Python SDK pointed at ZenMux:
  * multimodal input (image_url parts)
  * structured output via JSON mode + Pydantic validation
  * retry / rate-limit awareness via tenacity
  * streaming passthrough (tokens forwarded to LangGraph via stream.push_token)
  * vision capability guard — reroutes to VISION_FALLBACK if the routed model
    doesn't accept images

Design choice: we deliberately use JSON mode + Pydantic validator rather than
`response_format=json_schema`, because schema support varies by upstream provider
behind ZenMux while JSON mode works uniformly.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

import json_repair
from openai import OpenAI
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .models import VISION_FALLBACK, vision_capable
from .stream import push_token
from .vision import image_part_from_path, image_part_from_url, text_part

log = logging.getLogger(__name__)

ZENMUX_BASE_URL = os.getenv("ZENMUX_BASE_URL", "https://zenmux.ai/api/v1")
ZENMUX_API_KEY_ENV = "ZENMUX_API_KEY"

T = TypeVar("T", bound=BaseModel)


@dataclass
class CompletionResult:
    text: str
    finish_reason: str | None = None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _client() -> OpenAI:
    key = os.getenv(ZENMUX_API_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"Missing {ZENMUX_API_KEY_ENV}. Export your ZenMux API key before "
            f"starting the backend."
        )
    return OpenAI(api_key=key, base_url=ZENMUX_BASE_URL)


# ---------------------------------------------------------------------------
# Multimodal message construction
# ---------------------------------------------------------------------------

def _attach_images(messages: list[dict[str, Any]], images: list[str | Path]) -> list[dict[str, Any]]:
    """Append image parts to the last user message (merging into a parts array)."""
    if not images:
        return messages
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            target = messages[i]
            if isinstance(target.get("content"), str):
                target["content"] = [text_part(target["content"])]
            for img in images:
                s = str(img)
                if s.startswith(("http://", "https://", "data:")):
                    target["content"].append(image_part_from_url(s))
                else:
                    target["content"].append(image_part_from_path(s))
            return messages
    # No user message — create one carrying only images
    parts: list[dict[str, Any]] = []
    for img in images:
        s = str(img)
        if s.startswith(("http://", "https://", "data:")):
            parts.append(image_part_from_url(s))
        else:
            parts.append(image_part_from_path(s))
    messages.append({"role": "user", "content": parts})
    return messages


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------

class RetriableError(Exception):
    """Transient errors worth retrying (rate limits, upstream timeouts)."""


def _is_retriable(exc: BaseException) -> bool:
    from openai import APIConnectionError, APITimeoutError, RateLimitError
    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError, RetriableError)):
        return True
    code = getattr(exc, "status_code", None)
    return code in (408, 429, 500, 502, 503, 504)


_retry = retry(
    retry=retry_if_exception_type(RetriableError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    reraise=True,
)


# ---------------------------------------------------------------------------
# Core chat entry points
# ---------------------------------------------------------------------------

def _resolve_vision_model(model: str, has_images: bool) -> str:
    if not has_images:
        return model
    if vision_capable(model):
        return model
    log.warning(
        "Model %s is not vision-capable but images were provided; rerouting to %s.",
        model, VISION_FALLBACK,
    )
    return VISION_FALLBACK


def chat(
    model: str,
    messages: list[dict[str, Any]],
    *,
    images: list[str | Path] | None = None,
    temperature: float = 0.4,
    max_tokens: int | None = None,
    timeout: float | None = None,
    stream: bool = False,
    json_object: bool = False,
) -> str:
    """Plain-text completion. Returns assistant message content as string."""
    return chat_with_metadata(
        model,
        messages,
        images=images,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        stream=stream,
        json_object=json_object,
    ).text


def chat_with_metadata(
    model: str,
    messages: list[dict[str, Any]],
    *,
    images: list[str | Path] | None = None,
    temperature: float = 0.4,
    max_tokens: int | None = None,
    timeout: float | None = None,
    stream: bool = False,
    json_object: bool = False,
) -> CompletionResult:
    """Plain-text completion plus terminal metadata such as finish_reason."""
    msgs = [dict(m) for m in messages]
    if images:
        _attach_images(msgs, images)
    routed_model = _resolve_vision_model(model, has_images=bool(images))

    kwargs: dict[str, Any] = {
        "model": routed_model,
        "messages": msgs,
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if timeout is not None:
        kwargs["timeout"] = timeout
    if json_object:
        kwargs["response_format"] = {"type": "json_object"}

    if stream:
        return _stream_completion_with_metadata(**kwargs)
    return _oneshot_completion_with_metadata(**kwargs)


def chat_structured(
    model: str,
    messages: list[dict[str, Any]],
    schema: type[T],
    *,
    images: list[str | Path] | None = None,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout: float | None = None,
    max_attempts: int = 2,
    stream: bool = False,
) -> T:
    """Structured completion — returns a validated Pydantic model instance.

    Uses JSON mode + Pydantic validation; on validation failure, re-prompts with
    the validation error so the model can self-correct. When stream=True tokens
    are forwarded via push_token() as they arrive, but the return value is the
    full validated object.
    """
    schema_hint = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    sys_hint = {
        "role": "system",
        "content": (
            "Respond with a JSON object matching this schema exactly. "
            "Do not include prose outside the JSON. Schema:\n" + schema_hint
        ),
    }
    working = [sys_hint, *messages]

    last_err: Exception | None = None
    last_raw: str = ""
    for attempt in range(max_attempts):
        raw = chat(
            model,
            working,
            images=images,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            json_object=True,
            stream=stream,
        )
        last_raw = raw
        # 1. Strict parse
        try:
            return schema.model_validate_json(raw)
        except ValidationError as strict_err:
            last_err = strict_err
            # 2. Lenient parse via json_repair — handles unescaped quotes inside
            #    CJK string values, trailing commas, truncated braces, etc.
            try:
                repaired = json_repair.repair_json(raw, return_objects=True)
                if isinstance(repaired, (dict, list)):
                    return schema.model_validate(repaired)
            except (ValidationError, ValueError, TypeError) as repair_err:
                last_err = repair_err
            log.warning(
                "chat_structured attempt %d/%d failed for %s (strict+repaired): %s\n"
                "Raw (last 400 chars): %r",
                attempt + 1, max_attempts, schema.__name__, last_err, raw[-400:],
            )
            working = [
                sys_hint,
                *messages,
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "Your previous JSON failed schema validation with these errors:\n"
                        f"{last_err}\nReturn corrected JSON only. Escape any literal "
                        "double-quote characters inside string values as \\\"."
                    ),
                },
            ]
    assert last_err is not None
    log.error(
        "chat_structured gave up after %d attempts for %s. Final raw: %r",
        max_attempts, schema.__name__, last_raw,
    )
    raise last_err


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

@_retry
def _embeddings_call(model: str, inputs: list[str]) -> list[list[float]]:
    try:
        resp = _client().embeddings.create(model=model, input=inputs)
    except Exception as e:
        if _is_retriable(e):
            raise RetriableError from e
        raise
    return [list(item.embedding) for item in resp.data]


def embeddings(
    model: str,
    inputs: list[str],
    *,
    batch_size: int = 96,
) -> list[list[float]]:
    """Return one embedding vector per input string.

    Batches at `batch_size` to stay under typical provider per-request input caps.
    Empty input list returns an empty list without making a network call.
    """
    if not inputs:
        return []
    out: list[list[float]] = []
    for start in range(0, len(inputs), batch_size):
        chunk = inputs[start : start + batch_size]
        out.extend(_embeddings_call(model, chunk))
    return out


# ---------------------------------------------------------------------------
# Low-level completion functions (with retry)
# ---------------------------------------------------------------------------

@_retry
def _oneshot_completion_with_metadata(**kwargs: Any) -> CompletionResult:
    try:
        resp = _client().chat.completions.create(**kwargs)
    except Exception as e:
        if _is_retriable(e):
            raise RetriableError from e
        raise
    choice = resp.choices[0] if resp.choices else None
    text = choice.message.content if choice and choice.message else ""
    return CompletionResult(
        text=text or "",
        finish_reason=getattr(choice, "finish_reason", None),
    )


@_retry
def _stream_completion_with_metadata(**kwargs: Any) -> CompletionResult:
    kwargs["stream"] = True
    chunks: list[str] = []
    finish_reason: str | None = None
    try:
        for event in _client().chat.completions.create(**kwargs):
            choice = event.choices[0] if event.choices else None
            if choice and getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason
            delta = choice.delta if choice else None
            if not delta:
                continue
            piece = getattr(delta, "content", None) or ""
            if piece:
                chunks.append(piece)
                push_token(piece)
    except Exception as e:
        if _is_retriable(e):
            raise RetriableError from e
        raise
    return CompletionResult(text="".join(chunks), finish_reason=finish_reason)
