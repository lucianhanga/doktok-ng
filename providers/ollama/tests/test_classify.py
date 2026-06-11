"""OllamaCategoryClassifier: structured array output + JSON-repair fallback (httpx mocked)."""

from __future__ import annotations

import json
from typing import Any

from doktok_provider_ollama import OllamaCategoryClassifier


class _Resp:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]:
        return {"message": {"content": self._content, "thinking": "ignored"}}


def _patch(monkeypatch: Any, replies: list[str]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _Resp:
        calls.append(json)
        return _Resp(replies[len(calls) - 1])

    monkeypatch.setattr("doktok_provider_ollama.classify.httpx.post", fake_post)
    return calls


def test_parses_labels_and_dedupes(monkeypatch: Any) -> None:
    reply = json.dumps({"categories": ["Invoice", "invoice", "Finance"]})
    calls = _patch(monkeypatch, [reply])
    out = OllamaCategoryClassifier("primary", "repair", "http://x").classify("text", ["Finance"])
    assert out == ["Invoice", "Finance"]  # case-insensitive dedupe
    assert "Finance" in calls[0]["messages"][0]["content"]  # existing vocab passed in
    assert "think" not in calls[0]["options"]  # primary leaves thinking on


def test_repairs_invalid_json(monkeypatch: Any) -> None:
    good = json.dumps({"categories": ["Legal"]})
    calls = _patch(monkeypatch, ["not json at all", good])
    out = OllamaCategoryClassifier("primary", "repair", "http://x").classify("text", [])
    assert out == ["Legal"]
    assert len(calls) == 2 and calls[1]["model"] == "repair"
    assert calls[1]["options"]["think"] is False
