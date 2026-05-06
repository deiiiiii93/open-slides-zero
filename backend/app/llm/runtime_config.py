"""Request-scoped runtime LLM configuration.

The public app lets users bring their own ZenMux key. Keep that key in the
request context only: no graph state, checkpoint row, mirror file, or log should
need to carry it.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from fastapi import HTTPException, Request

ZENMUX_KEY_HEADER = "x-osz-zenmux-key"
ZENMUX_BASE_URL_HEADER = "x-osz-zenmux-base-url"
MODEL_OVERRIDES_HEADER = "x-osz-model-overrides"
THINKING_OVERRIDES_HEADER = "x-osz-thinking-effort-overrides"
DEFAULT_ZENMUX_BASE_URL = "https://zenmux.ai/api/v1"
DEFAULT_ZENMUX_VERTEX_BASE_URL = "https://zenmux.ai/api/vertex-ai"
ZENMUX_API_KEY_ENV = "ZENMUX_API_KEY"
ZENMUX_BASE_URL_ENV = "ZENMUX_BASE_URL"
ZENMUX_VERTEX_BASE_URL_ENV = "ZENMUX_VERTEX_BASE_URL"
RUNTIME_CONFIG_ID_KEY = "osz_runtime_config_id"


@dataclass(frozen=True)
class RuntimeLLMConfig:
    zenmux_api_key: str | None = None
    zenmux_base_url: str | None = None
    model_overrides: dict[str, str] = field(default_factory=dict)
    thinking_effort_overrides: dict[str, str] = field(default_factory=dict)


_runtime_config: ContextVar[RuntimeLLMConfig] = ContextVar(
    "osz_runtime_llm_config",
    default=RuntimeLLMConfig(),
)
_runtime_config_id: ContextVar[str | None] = ContextVar(
    "osz_runtime_llm_config_id",
    default=None,
)
_registered_runtime_configs: dict[str, RuntimeLLMConfig] = {}
_registered_runtime_configs_lock = threading.Lock()


def current_runtime_config() -> RuntimeLLMConfig:
    return _runtime_config.get()


def register_runtime_config(config: RuntimeLLMConfig) -> str:
    runtime_id = secrets.token_urlsafe(16)
    with _registered_runtime_configs_lock:
        _registered_runtime_configs[runtime_id] = config
    return runtime_id


def unregister_runtime_config(runtime_id: str | None) -> None:
    if runtime_id is None:
        return
    with _registered_runtime_configs_lock:
        _registered_runtime_configs.pop(runtime_id, None)


@contextmanager
def use_runtime_config(config: RuntimeLLMConfig | Any, *, register: bool = True) -> Iterator[str | None]:
    if not isinstance(config, RuntimeLLMConfig):
        yield None
        return
    runtime_id: str | None = None
    id_token = None
    if register:
        runtime_id = register_runtime_config(config)
        id_token = _runtime_config_id.set(runtime_id)
    token = _runtime_config.set(config)
    try:
        yield runtime_id
    finally:
        try:
            _runtime_config.reset(token)
        except ValueError:
            # StreamingResponse generators can be resumed by Starlette in a
            # sibling context. The request is ending, so leaving that sibling
            # context to be discarded is safer than failing the stream.
            pass
        if id_token is not None:
            try:
                _runtime_config_id.reset(id_token)
            except ValueError:
                pass
        unregister_runtime_config(runtime_id)


def current_runtime_config_id() -> str | None:
    return _runtime_config_id.get()


def attach_runtime_config_id(config: dict[str, Any], runtime_id: str | None = None) -> dict[str, Any]:
    runtime_id = runtime_id or current_runtime_config_id()
    if not runtime_id:
        return config
    next_config = dict(config)
    next_config["configurable"] = {
        **dict(config.get("configurable") or {}),
        RUNTIME_CONFIG_ID_KEY: runtime_id,
    }
    return next_config


def runtime_config_from_graph_config(config: Any) -> RuntimeLLMConfig | None:
    if not isinstance(config, Mapping):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    runtime_id = configurable.get(RUNTIME_CONFIG_ID_KEY)
    if not isinstance(runtime_id, str) or not runtime_id:
        return None
    with _registered_runtime_configs_lock:
        return _registered_runtime_configs.get(runtime_id)


def _parse_json_object(raw: str | None, label: str) -> dict[str, str]:
    if raw is None or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"{label} must be valid JSON.") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{label} must be a JSON object.")
    normalized: dict[str, str] = {}
    for key, item in value.items():
        clean_key = str(key).strip()
        clean_value = str(item).strip()
        if clean_key and clean_value:
            normalized[clean_key] = clean_value
    return normalized


def runtime_config_from_headers(headers: Mapping[str, str]) -> RuntimeLLMConfig:
    key = (headers.get(ZENMUX_KEY_HEADER) or "").strip() or None
    base_url = (headers.get(ZENMUX_BASE_URL_HEADER) or "").strip() or None
    return RuntimeLLMConfig(
        zenmux_api_key=key,
        zenmux_base_url=base_url,
        model_overrides=_parse_json_object(
            headers.get(MODEL_OVERRIDES_HEADER),
            "model overrides",
        ),
        thinking_effort_overrides=_parse_json_object(
            headers.get(THINKING_OVERRIDES_HEADER),
            "thinking effort overrides",
        ),
    )


def runtime_config_from_request(request: Request) -> RuntimeLLMConfig:
    return runtime_config_from_headers(request.headers)


def effective_zenmux_api_key() -> str | None:
    return current_runtime_config().zenmux_api_key or os.getenv(ZENMUX_API_KEY_ENV)


def effective_zenmux_base_url() -> str:
    return (
        current_runtime_config().zenmux_base_url
        or os.getenv(ZENMUX_BASE_URL_ENV)
        or DEFAULT_ZENMUX_BASE_URL
    )


def effective_zenmux_vertex_base_url() -> str:
    configured = current_runtime_config().zenmux_base_url
    if configured:
        base = configured.rstrip("/")
        if base.endswith("/api/v1"):
            return base.removesuffix("/api/v1") + "/api/vertex-ai"
        if base.endswith("/v1"):
            return base.removesuffix("/v1") + "/vertex-ai"
        return base
    return os.getenv(ZENMUX_VERTEX_BASE_URL_ENV, DEFAULT_ZENMUX_VERTEX_BASE_URL)


def require_zenmux_api_key() -> str:
    key = effective_zenmux_api_key()
    if not key:
        raise HTTPException(
            status_code=401,
            detail="ZenMux API key is required. Add it in Config before generating.",
        )
    return key


def get_model_override(node: str) -> str | None:
    value = current_runtime_config().model_overrides.get(node)
    return value.strip() if isinstance(value, str) and value.strip() else None


def get_thinking_effort_override(stage: str) -> str | None:
    value = current_runtime_config().thinking_effort_overrides.get(stage)
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


def redacted_runtime_secrets() -> list[str]:
    cfg = current_runtime_config()
    secrets = [cfg.zenmux_api_key, os.getenv(ZENMUX_API_KEY_ENV)]
    return [secret for secret in secrets if secret]


def redact_secrets(value: Any) -> str:
    text = str(value)
    for secret in redacted_runtime_secrets():
        text = text.replace(secret, "[REDACTED]")
    return text
