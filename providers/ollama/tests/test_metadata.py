"""OllamaMetadataExtractor: structured output + JSON-repair fallback (httpx mocked)."""

from __future__ import annotations

import json
from typing import Any

from doktok_provider_ollama import OllamaMetadataExtractor


class _Resp:
    def __init__(self, content: str, extra: dict[str, Any] | None = None) -> None:
        self._content = content
        self._extra = extra or {}

    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "message": {"content": self._content, "thinking": "ignored reasoning"}
        }
        body.update(self._extra)
        return body


def _patch(monkeypatch: Any, replies: list[str]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _Resp:
        calls.append(json)
        return _Resp(replies[len(calls) - 1])

    monkeypatch.setattr("doktok_provider_ollama.metadata.httpx.post", fake_post)
    return calls


_GOOD = json.dumps(
    {"title": "T", "document_date": "2026-01-01", "document_location": "Berlin", "summary": "S"}
)


def test_parses_structured_output_without_repair(monkeypatch: Any) -> None:
    calls = _patch(monkeypatch, [_GOOD])
    ex = OllamaMetadataExtractor("primary", "repair", "http://x")
    meta = ex.extract("some document text")
    assert meta.title == "T" and meta.document_date == "2026-01-01" and meta.location == "Berlin"
    assert len(calls) == 1  # no repair needed
    assert "think" not in calls[0]  # primary (think=True) omits the top-level think field


def test_think_false_hard_disables_thinking(monkeypatch: Any) -> None:
    calls = _patch(monkeypatch, [_GOOD])
    OllamaMetadataExtractor("dense", "repair", "http://x", think=False).extract("text")
    assert calls[0]["think"] is False  # dense fast-path disables thinking (top-level)


def test_falls_back_to_repair_model_on_invalid_json(monkeypatch: Any) -> None:
    calls = _patch(monkeypatch, ["here is your answer: not json", _GOOD])
    ex = OllamaMetadataExtractor("primary", "repair", "http://x")
    meta = ex.extract("text")
    assert meta.title == "T"
    assert len(calls) == 2
    assert calls[0]["model"] == "primary" and calls[1]["model"] == "repair"
    assert calls[1]["think"] is False  # repair model disables thinking (top-level)


def test_reports_token_usage_from_response_counts(monkeypatch: Any) -> None:
    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _Resp:
        return _Resp(_GOOD, extra={"prompt_eval_count": 320, "eval_count": 64})

    monkeypatch.setattr("doktok_provider_ollama.metadata.httpx.post", fake_post)
    ex = OllamaMetadataExtractor("primary", "repair", "http://x")
    ex.extract("some document text")
    usage = ex.get_last_usage()
    assert usage is not None
    assert usage.prompt_tokens == 320
    assert usage.answer_tokens == 64
    assert usage.total_tokens == 384
    assert usage.estimated is False
    assert ex.model == "primary"


def test_usage_estimated_when_counts_absent(monkeypatch: Any) -> None:
    _patch(monkeypatch, [_GOOD])  # _Resp without eval_count/prompt_eval_count
    ex = OllamaMetadataExtractor("primary", "repair", "http://x")
    ex.extract("some document text")
    usage = ex.get_last_usage()
    assert usage is not None and usage.estimated is True
    assert usage.prompt_tokens == 0  # no provider counter -> 0
