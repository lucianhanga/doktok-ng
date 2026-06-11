"""Unit tests for the Ollama OCR provider (httpx mocked; no server)."""

from __future__ import annotations

from typing import Any

from doktok_provider_ollama import OllamaVisionOcr
from doktok_provider_ollama.ocr import trim_runaway_repetition


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]:
        return self._body


def _capture(monkeypatch: Any, body: dict[str, Any]) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _FakeResponse:
        captured["json"] = json
        return _FakeResponse(body)

    monkeypatch.setattr("doktok_provider_ollama.ocr.httpx.post", fake_post)
    return captured


def test_sends_bounded_context_and_keep_alive(monkeypatch: Any) -> None:
    captured = _capture(monkeypatch, {"response": "page text", "done": True})
    ocr = OllamaVisionOcr("glm-ocr", "http://localhost:11434", num_ctx=8192, num_predict=4096)
    result = ocr.ocr_image(b"\x89PNG fake")
    assert result.text == "page text"
    assert captured["json"]["options"]["num_ctx"] == 8192
    assert captured["json"]["options"]["num_predict"] == 4096
    assert captured["json"]["options"]["repeat_penalty"] == 1.3  # breaks OCR repeat-loops
    assert captured["json"]["keep_alive"] == "5m"


def test_truncated_page_keeps_partial_text_does_not_fail(monkeypatch: Any) -> None:
    # A page that hits the output cap (done=false) must not fail the document - keep what we got.
    _capture(monkeypatch, {"response": "partial transcription...", "done": False})
    result = OllamaVisionOcr("glm-ocr", "http://localhost:11434").ocr_image(b"img")
    assert result.text == "partial transcription..."


def test_collapses_runaway_repetition_in_ocr_output(monkeypatch: Any) -> None:
    garbage = "SIGNAL IDUNA header\n" + "\n".join(["1.0 JAN. 2025", "SSOS MAL"] * 60)
    _capture(monkeypatch, {"response": garbage, "done": True})
    result = OllamaVisionOcr("glm-ocr", "http://localhost:11434").ocr_image(b"img")
    assert "SIGNAL IDUNA header" in result.text  # the real text survives
    assert result.text.count("SSOS MAL") <= 3  # the 60x loop collapsed
    assert len(result.text) < len(garbage) / 5


def test_trim_keeps_distinct_lines() -> None:
    # Real content (distinct lines, incl. a normal table) is never collapsed.
    normal = "Invoice 1 10.00\nInvoice 2 20.00\nInvoice 3 30.00\nTotal 60.00"
    assert trim_runaway_repetition(normal) == normal
