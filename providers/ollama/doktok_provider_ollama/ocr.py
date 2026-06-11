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
    """``OcrExtractor`` backed by an Ollama vision model.

    A single page needs only a modest context (image tiles + prompt + output ~= 4.4k tokens); the
    default ``num_ctx`` of 8192 avoids the ~1 GB KV cache a 32k context reserves. ``num_predict``
    caps per-page output so a garbled/dense page can't loop unbounded.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        timeout: float = 600.0,
        num_ctx: int = 8192,
        num_predict: int = 4096,
        keep_alive: str = "5m",
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._num_ctx = num_ctx
        self._num_predict = num_predict
        self._keep_alive = keep_alive

    def ocr_image(self, image_png: bytes) -> OcrPageResult:
        payload = {
            "model": self._model,
            "prompt": OCR_PROMPT,
            "images": [base64.b64encode(image_png).decode("ascii")],
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": {
                "temperature": 0,
                "num_ctx": self._num_ctx,
                "num_predict": self._num_predict,
            },
        }
        response = httpx.post(f"{self._base_url}/api/generate", json=payload, timeout=self._timeout)
        response.raise_for_status()
        body = response.json()
        if body.get("done") is False:
            raise RuntimeError("OCR did not complete (num_predict cap hit on a dense/garbled page)")
        text = str(body.get("response", "")).strip()
        return OcrPageResult(text=text, confidence=None)
