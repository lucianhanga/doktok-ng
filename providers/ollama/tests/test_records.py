"""OllamaRecordExtractor: structured transaction array + JSON-repair fallback (httpx mocked)."""

from __future__ import annotations

import json
from typing import Any

from doktok_provider_ollama import OllamaRecordExtractor


class _Resp:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]:
        return {"message": {"content": self._content}}


def _patch(monkeypatch: Any, replies: list[str]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _Resp:
        calls.append(json)
        return _Resp(replies[len(calls) - 1])

    monkeypatch.setattr("doktok_provider_ollama.records.httpx.post", fake_post)
    return calls


_GOOD = json.dumps(
    {
        "transactions": [
            {"date": "2026-01-05", "merchant": "Block House", "amount": "45.00", "currency": "EUR"},
            {"date": "2026-01-12", "merchant": "Amazon", "amount": "23.50", "currency": "EUR"},
        ]
    }
)


def test_parses_transactions(monkeypatch: Any) -> None:
    calls = _patch(monkeypatch, [_GOOD])
    out = OllamaRecordExtractor("dense", "repair", "http://x").extract("statement")
    assert [t.merchant for t in out] == ["Block House", "Amazon"]
    assert out[0].amount == "45.00" and out[0].currency == "EUR"
    assert calls[0]["think"] is False  # dense fast-path, top-level think


def test_empty_for_non_financial(monkeypatch: Any) -> None:
    _patch(monkeypatch, [json.dumps({"transactions": []})])
    assert OllamaRecordExtractor("dense", "repair", "http://x").extract("a memo") == []


def test_repairs_invalid_json(monkeypatch: Any) -> None:
    calls = _patch(monkeypatch, ["not json", _GOOD])
    out = OllamaRecordExtractor("dense", "repair", "http://x").extract("statement")
    assert len(out) == 2 and len(calls) == 2 and calls[1]["model"] == "repair"
