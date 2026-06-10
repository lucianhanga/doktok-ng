"""OCR via a local Ollama vision model (ADR-0003, configurable DOKTOK_OCR_MODEL).

Sends a page image to Ollama's ``/api/generate`` with a faithful-transcription prompt. Talks only to
the local Ollama endpoint (no external egress). The model does not report a confidence, so
``confidence`` is left ``None``.
"""

from __future__ import annotations

import base64

import httpx
from doktok_contracts.media import OcrPageResult

OCR_PROMPT = (
    "Extract all text from this document image exactly as it appears. "
    "Preserve reading order (top to bottom, left to right; columns in column order). "
    "Format headings as Markdown headings and tables as Markdown tables. "
    "Do not summarize, interpret, or add commentary. Output only the extracted content."
)


class OllamaVisionOcr:
    """``OcrExtractor`` backed by an Ollama vision model."""

    def __init__(self, model: str, base_url: str, *, timeout: float = 180.0) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def ocr_image(self, image_png: bytes) -> OcrPageResult:
        payload = {
            "model": self._model,
            "prompt": OCR_PROMPT,
            "images": [base64.b64encode(image_png).decode("ascii")],
            "stream": False,
            "options": {"temperature": 0},
        }
        response = httpx.post(f"{self._base_url}/api/generate", json=payload, timeout=self._timeout)
        response.raise_for_status()
        text = str(response.json().get("response", "")).strip()
        return OcrPageResult(text=text, confidence=None)
