"""Unit tests for the Ollama embedding provider (httpx mocked; no server needed)."""

from __future__ import annotations

from typing import Any

from doktok_provider_ollama import OllamaEmbeddingProvider


class _FakeResponse:
    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]:
        return {"embeddings": [[0.1, 0.2]]}


def _capture(monkeypatch: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse()

    monkeypatch.setattr("doktok_provider_ollama.embeddings.httpx.post", fake_post)
    return captured


def test_keep_alive_pins_the_embedding_model_when_set(monkeypatch: Any) -> None:
    captured = _capture(monkeypatch)
    provider = OllamaEmbeddingProvider(
        "qwen3-embedding:0.6b", "http://localhost:11434", keep_alive="30m"
    )
    assert provider.embed(["hello"]) == [[0.1, 0.2]]
    assert captured["json"]["keep_alive"] == "30m"
    assert captured["json"]["model"] == "qwen3-embedding:0.6b"


def test_keep_alive_omitted_by_default(monkeypatch: Any) -> None:
    captured = _capture(monkeypatch)
    OllamaEmbeddingProvider("qwen3-embedding:0.6b", "http://localhost:11434").embed(["x"])
    assert "keep_alive" not in captured["json"]


def test_empty_input_skips_the_call(monkeypatch: Any) -> None:
    captured = _capture(monkeypatch)
    assert OllamaEmbeddingProvider("m", "http://localhost:11434").embed([]) == []
    assert captured == {}  # no HTTP call for an empty batch
