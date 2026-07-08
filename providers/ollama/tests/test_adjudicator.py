"""OllamaEntityMergeAdjudicator: structured JSON verdict + thinking hard-disabled (#510).

The merge adjudication is a fast yes/no JSON classification - it must NEVER spend reasoning
tokens (that would be slow + memory-heavy on a reasoning pipeline model). These tests lock that:
every adjudication call (and its JSON-repair fallback) sends top-level ``think: false``.
"""

from __future__ import annotations

import json
from typing import Any

from doktok_contracts.schemas import EntityProfile
from doktok_provider_ollama.adjudicator import OllamaEntityMergeAdjudicator


class _Resp:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]:
        return {"message": {"content": self._content, "thinking": "ignored reasoning"}}


def _patch(monkeypatch: Any, replies: list[str]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _Resp:
        calls.append(json)
        return _Resp(replies[len(calls) - 1])

    monkeypatch.setattr("doktok_provider_ollama.adjudicator.httpx.post", fake_post)
    return calls


_VERDICT = json.dumps(
    {"same": True, "canonical": "Lucian Hanga", "confidence": 0.9, "reason": "same person"}
)


def _profiles() -> tuple[EntityProfile, EntityProfile]:
    a = EntityProfile(
        entity_id="e1", entity_type="PERSON", normalized_value="lucian hanga", neighbors=[]
    )
    b = EntityProfile(
        entity_id="e2", entity_type="PERSON", normalized_value="hanja lucian", neighbors=[]
    )
    return a, b


def test_adjudicate_parses_verdict_and_disables_thinking(monkeypatch: Any) -> None:
    calls = _patch(monkeypatch, [_VERDICT])
    a, b = _profiles()
    verdict = OllamaEntityMergeAdjudicator("dense", "repair", "http://x").adjudicate(a, b)
    assert verdict.same is True and verdict.confidence == 0.9
    assert len(calls) == 1  # clean JSON, no repair pass
    assert calls[0]["think"] is False  # no reasoning: thinking hard-disabled on the adjudication


def test_repair_pass_also_disables_thinking(monkeypatch: Any) -> None:
    calls = _patch(monkeypatch, ["here is the answer: not json", _VERDICT])
    a, b = _profiles()
    OllamaEntityMergeAdjudicator("dense", "repair", "http://x").adjudicate(a, b)
    assert len(calls) == 2  # invalid JSON -> repair
    assert calls[0]["think"] is False and calls[1]["think"] is False  # neither call reasons
