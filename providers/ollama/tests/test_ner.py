"""OllamaEntityNerExtractor: structured PERSON/ORG/GPE output + JSON repair (httpx mocked)."""

from __future__ import annotations

import json
from typing import Any

from doktok_contracts.schemas import EntityType
from doktok_provider_ollama import OllamaEntityNerExtractor


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

    monkeypatch.setattr("doktok_provider_ollama.ner.httpx.post", fake_post)
    return calls


def test_parses_each_type_and_dedupes(monkeypatch: Any) -> None:
    reply = json.dumps(
        {
            "people": ["Angela Merkel", "angela merkel"],  # dup (case-insensitive)
            "organizations": ["Siemens"],
            "places": ["Berlin"],
            "job_titles": ["Bundeskanzlerin", "Software Engineer"],  # multilingual (#518 P2)
        }
    )
    _patch(monkeypatch, [reply])
    out = OllamaEntityNerExtractor("primary", "repair", "http://x").extract("text")

    pairs = [(e.entity_type, e.entity_text) for e in out]
    assert (EntityType.PERSON, "Angela Merkel") in pairs
    assert (EntityType.ORG, "Siemens") in pairs
    assert (EntityType.GPE, "Berlin") in pairs
    assert (EntityType.JOB_TITLE, "Bundeskanzlerin") in pairs
    assert (EntityType.JOB_TITLE, "Software Engineer") in pairs
    assert len([p for p in pairs if p[0] == EntityType.PERSON]) == 1  # deduped


def test_repairs_invalid_json(monkeypatch: Any) -> None:
    good = json.dumps({"people": ["Bob"], "organizations": [], "places": []})
    calls = _patch(monkeypatch, ["not json", good])
    out = OllamaEntityNerExtractor("primary", "repair", "http://x").extract("text")

    assert [(e.entity_type, e.entity_text) for e in out] == [(EntityType.PERSON, "Bob")]
    assert len(calls) == 2 and calls[1]["model"] == "repair" and calls[1]["think"] is False


def test_missing_arrays_yield_no_entities(monkeypatch: Any) -> None:
    _patch(monkeypatch, [json.dumps({"people": []})])  # organizations/places/job_titles absent
    out = OllamaEntityNerExtractor("primary", "repair", "http://x").extract("text")
    assert out == []


def test_schema_requests_job_titles(monkeypatch: Any) -> None:
    # The structured-output schema must ask the model for job_titles so the field is never omitted.
    reply = json.dumps({"people": [], "organizations": [], "places": [], "job_titles": []})
    calls = _patch(monkeypatch, [reply])
    OllamaEntityNerExtractor("primary", "repair", "http://x").extract("text")
    schema = calls[0]["format"]
    assert "job_titles" in schema["properties"]
    assert "job_titles" in schema["required"]
