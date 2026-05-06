from __future__ import annotations

from typing import TypedDict
from typing import Any

import pytest
from fastapi import HTTPException
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.graph.graph import _runtime_node
from app.llm import zenmux
from app.llm.models import get_model
from app.llm.runtime_config import (
    RuntimeLLMConfig,
    attach_runtime_config_id,
    register_runtime_config,
    unregister_runtime_config,
    use_runtime_config,
)


class _Message:
    def __init__(self, content: str = "ok") -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str = "ok") -> None:
        self.message = _Message(content)
        self.finish_reason = "stop"


class _Response:
    def __init__(self, content: str = "ok") -> None:
        self.choices = [_Choice(content)]


class _FakeCompletions:
    def __init__(self, content: str = "ok") -> None:
        self.calls: list[dict[str, Any]] = []
        self.content = content

    def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        return _Response(self.content)


class _FakeChat:
    def __init__(self, content: str = "ok") -> None:
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content: str = "ok") -> None:
        self.chat = _FakeChat(content)


class _StructuredPayload(BaseModel):
    value: str


class _MiniState(TypedDict):
    text: str


def test_html_critic_defaults_to_deepseek_flash():
    assert get_model("html.critic") == "deepseek/deepseek-v4-flash"


def test_kimi_temperature_is_forced_to_one(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(zenmux, "_client", lambda: client)

    result = zenmux.chat_with_metadata(
        "moonshotai/kimi-k2.6",
        [{"role": "user", "content": "hello"}],
        temperature=0.2,
    )

    assert result.text == "ok"
    assert client.chat.completions.calls[0]["temperature"] == 1


def test_non_kimi_temperature_is_preserved(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(zenmux, "_client", lambda: client)

    zenmux.chat_with_metadata(
        "anthropic/claude-sonnet-4.6",
        [{"role": "user", "content": "hello"}],
        temperature=0.2,
    )

    assert client.chat.completions.calls[0]["temperature"] == 0.2


def test_default_chat_timeout_is_applied(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(zenmux, "_client", lambda: client)
    monkeypatch.setenv("ZENMUX_CHAT_TIMEOUT_SECONDS", "45")

    zenmux.chat_with_metadata(
        "anthropic/claude-sonnet-4.6",
        [{"role": "user", "content": "hello"}],
    )

    assert client.chat.completions.calls[0]["timeout"] == 45


def test_explicit_chat_timeout_wins(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(zenmux, "_client", lambda: client)
    monkeypatch.setenv("ZENMUX_CHAT_TIMEOUT_SECONDS", "45")

    zenmux.chat_with_metadata(
        "anthropic/claude-sonnet-4.6",
        [{"role": "user", "content": "hello"}],
        timeout=12,
    )

    assert client.chat.completions.calls[0]["timeout"] == 12


def test_reasoning_effort_is_forwarded(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(zenmux, "_client", lambda: client)

    zenmux.chat_with_metadata(
        "openai/gpt-5.4",
        [{"role": "user", "content": "hello"}],
        reasoning_effort="high",
    )

    assert client.chat.completions.calls[0]["reasoning_effort"] == "high"


def test_o_series_temperature_is_omitted_when_reasoning_effort_is_set(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(zenmux, "_client", lambda: client)

    zenmux.chat_with_metadata(
        "openai/o3",
        [{"role": "user", "content": "hello"}],
        temperature=0.2,
        reasoning_effort="low",
    )

    call = client.chat.completions.calls[0]
    assert call["reasoning_effort"] == "low"
    assert "temperature" not in call


def test_chat_structured_forwards_reasoning_effort(monkeypatch):
    client = _FakeClient(content='{"value":"ok"}')
    monkeypatch.setattr(zenmux, "_client", lambda: client)

    result = zenmux.chat_structured(
        "openai/gpt-5.4",
        [{"role": "user", "content": "hello"}],
        _StructuredPayload,
        reasoning_effort="medium",
    )

    call = client.chat.completions.calls[0]
    assert result.value == "ok"
    assert call["reasoning_effort"] == "medium"
    assert call["response_format"] == {"type": "json_object"}


def test_runtime_zenmux_config_overrides_env_client(monkeypatch):
    client = _FakeClient()
    calls: list[tuple[str, str]] = []

    def fake_client_for(key: str, base_url: str):
        calls.append((key, base_url))
        return client

    monkeypatch.setenv("ZENMUX_API_KEY", "env-key")
    monkeypatch.setenv("ZENMUX_BASE_URL", "https://env.example/v1")
    monkeypatch.setattr(zenmux, "_client_for", fake_client_for)

    with use_runtime_config(
        RuntimeLLMConfig(
            zenmux_api_key="runtime-key",
            zenmux_base_url="https://runtime.example/v1",
        )
    ):
        zenmux.chat_with_metadata(
            "anthropic/claude-sonnet-4.6",
            [{"role": "user", "content": "hello"}],
        )

    assert calls == [("runtime-key", "https://runtime.example/v1")]


def test_runtime_config_reaches_langgraph_worker_thread(monkeypatch):
    client = _FakeClient()
    calls: list[tuple[str, str]] = []

    def fake_client_for(key: str, base_url: str):
        calls.append((key, base_url))
        return client

    def node(_state: _MiniState) -> dict[str, str]:
        text = zenmux.chat_with_metadata(
            "anthropic/claude-sonnet-4.6",
            [{"role": "user", "content": "hello"}],
        ).text
        return {"text": text}

    monkeypatch.delenv("ZENMUX_API_KEY", raising=False)
    monkeypatch.setattr(zenmux, "_client_for", fake_client_for)

    graph = StateGraph(_MiniState)
    graph.add_node("node", _runtime_node(node))
    graph.add_edge(START, "node")
    graph.add_edge("node", END)
    compiled = graph.compile()

    with use_runtime_config(
        RuntimeLLMConfig(
            zenmux_api_key="runtime-key",
            zenmux_base_url="https://runtime.example/v1",
        )
    ):
        chunks = list(
            compiled.stream(
                {"text": ""},
                attach_runtime_config_id({"configurable": {"thread_id": "runtime-test"}}),
                stream_mode=["updates"],
            )
        )

    assert chunks[-1] == ("updates", {"node": {"text": "ok"}})
    assert calls == [("runtime-key", "https://runtime.example/v1")]


def test_explicit_runtime_config_id_reaches_stream_without_contextvar(monkeypatch):
    client = _FakeClient()
    calls: list[tuple[str, str]] = []

    def fake_client_for(key: str, base_url: str):
        calls.append((key, base_url))
        return client

    def node(_state: _MiniState) -> dict[str, str]:
        text = zenmux.chat_with_metadata(
            "anthropic/claude-sonnet-4.6",
            [{"role": "user", "content": "hello"}],
        ).text
        return {"text": text}

    monkeypatch.delenv("ZENMUX_API_KEY", raising=False)
    monkeypatch.setattr(zenmux, "_client_for", fake_client_for)

    graph = StateGraph(_MiniState)
    graph.add_node("node", _runtime_node(node))
    graph.add_edge(START, "node")
    graph.add_edge("node", END)
    compiled = graph.compile()

    runtime_id = register_runtime_config(
        RuntimeLLMConfig(
            zenmux_api_key="explicit-runtime-key",
            zenmux_base_url="https://explicit.example/v1",
        )
    )
    try:
        chunks = list(
            compiled.stream(
                {"text": ""},
                attach_runtime_config_id(
                    {"configurable": {"thread_id": "runtime-test"}},
                    runtime_id,
                ),
                stream_mode=["updates"],
            )
        )
    finally:
        unregister_runtime_config(runtime_id)

    assert chunks[-1] == ("updates", {"node": {"text": "ok"}})
    assert calls == [("explicit-runtime-key", "https://explicit.example/v1")]


def test_missing_runtime_and_env_key_raises_clear_http_error(monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY", raising=False)

    with pytest.raises(HTTPException) as exc:
        zenmux.chat_with_metadata(
            "anthropic/claude-sonnet-4.6",
            [{"role": "user", "content": "hello"}],
        )

    assert exc.value.status_code == 401
    assert "ZenMux API key is required" in str(exc.value.detail)
