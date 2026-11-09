"""Unit tests for the Ollama embedding provider (httpx mocked; no server needed)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

from doktok_provider_ollama import OllamaEmbeddingProvider


class _FakeResponse:
    def __init__(self, body: dict[str, Any] | None = None) -> None:
        self._body = body or {"embeddings": [[0.1, 0.2]]}

    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]:
        return self._body


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


def test_num_ctx_is_sent_as_option_when_set(monkeypatch: Any) -> None:
    captured = _capture(monkeypatch)
    OllamaEmbeddingProvider("m", "http://localhost:11434", num_ctx=1024).embed(["x"])
    assert captured["json"]["options"]["num_ctx"] == 1024


def test_num_ctx_omitted_by_default(monkeypatch: Any) -> None:
    captured = _capture(monkeypatch)
    OllamaEmbeddingProvider("m", "http://localhost:11434").embed(["x"])
    assert "options" not in captured["json"]


def test_usage_is_thread_confined_for_concurrent_calls(monkeypatch: Any) -> None:
    processed_barrier = Barrier(2)

    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _FakeResponse:
        prompt_tokens = 25 if json["input"] == ["doc-a"] else 50
        return _FakeResponse({"embeddings": [[0.1, 0.2]], "prompt_eval_count": prompt_tokens})

    monkeypatch.setattr("doktok_provider_ollama.embeddings.httpx.post", fake_post)
    provider = OllamaEmbeddingProvider("m", "http://localhost:11434")

    def embed(text: str) -> int | None:
        provider.embed([text])
        processed_barrier.wait(timeout=2)
        usage = provider.get_last_usage()
        return usage.prompt_tokens if usage is not None else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(embed, ["doc-a", "doc-b"]))

    assert results == [25, 50]
