from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.llm import zenmux
from app.llm.models import get_model


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
