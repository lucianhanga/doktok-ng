"""Unit tests for the Ollama chat provider (httpx mocked; no server needed)."""

from __future__ import annotations

from typing import Any

from doktok_provider_ollama import OllamaChatModelProvider


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]:
        return {"response": "ok"}

    @property
    def sent(self) -> dict[str, Any]:
        return self._payload


def _capture(monkeypatch: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse(json)

    monkeypatch.setattr("doktok_provider_ollama.chat.httpx.post", fake_post)
    return captured


def test_num_ctx_is_sent_when_set(monkeypatch: Any) -> None:
    captured = _capture(monkeypatch)
    provider = OllamaChatModelProvider("qwen", "http://localhost:11434", num_ctx=32768)
    assert provider.complete("hello") == "ok"
    assert captured["json"]["options"]["num_ctx"] == 32768
    assert captured["json"]["options"]["temperature"] == 0


def test_num_ctx_omitted_when_not_set(monkeypatch: Any) -> None:
    captured = _capture(monkeypatch)
    OllamaChatModelProvider("qwen", "http://localhost:11434").complete("hello")
    assert "num_ctx" not in captured["json"]["options"]
