"""Chat/completion via a local Ollama model (DOKTOK_DEFAULT_MODEL).

Talks only to the local Ollama endpoint (no external egress). Used for the OCR-vs-embedded text
judge (M5.x) and RAG answering (M6).
"""

from __future__ import annotations

import httpx


class OllamaChatModelProvider:
    """``ChatModelProvider`` backed by Ollama's ``/api/generate`` endpoint."""

    def __init__(self, model: str, base_url: str, *, timeout: float = 600.0) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def complete(self, prompt: str) -> str:
        response = httpx.post(
            f"{self._base_url}/api/generate",
            json={
                "model": self._model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        return str(response.json().get("response", "")).strip()
