from __future__ import annotations

from typing import Any

from app.llm import zenmux


class _Message:
    content = "ok"


class _Choice:
    message = _Message()
    finish_reason = "stop"


class _Response:
    choices = [_Choice()]


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        return _Response()


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


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
