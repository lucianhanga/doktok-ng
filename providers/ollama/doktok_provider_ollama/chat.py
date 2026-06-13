"""Chat/completion via a local Ollama model (DOKTOK_DEFAULT_MODEL).

Talks only to the local Ollama endpoint (no external egress). Used for the OCR-vs-embedded text
judge (M5.x) and RAG answering (M6).
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
from doktok_contracts.media import ChatChunk


class OllamaChatModelProvider:
    """``ChatModelProvider`` backed by Ollama's ``/api/generate`` endpoint."""

    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        timeout: float = 600.0,
        num_ctx: int | None = None,
        num_predict: int | None = None,
        keep_alive: str | None = None,
        think: bool = False,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._num_ctx = num_ctx
        # Cap output for short-response callers (e.g. the listwise reranker emits a tiny array).
        self._num_predict = num_predict
        # Residency hint: keep the (large) RAG model warm so idle gaps don't trigger a cold reload.
        self._keep_alive = keep_alive
        # Whether the model reasons before answering (reasoning density off -> False). No structured
        # `format` here, so toggling think is always safe.
        self._think = think

    def complete(self, prompt: str) -> str:
        options: dict[str, object] = {"temperature": 0}
        if self._num_ctx is not None:
            options["num_ctx"] = self._num_ctx
        if self._num_predict is not None:
            options["num_predict"] = self._num_predict
        payload: dict[str, object] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "think": self._think,
            "options": options,
        }
        if self._keep_alive is not None:
            payload["keep_alive"] = self._keep_alive
        response = httpx.post(f"{self._base_url}/api/generate", json=payload, timeout=self._timeout)
        response.raise_for_status()
        return str(response.json().get("response", "")).strip()

    def stream_complete(self, prompt: str, *, think: bool = False) -> Iterator[ChatChunk]:
        """Stream the answer via /api/chat (NDJSON). Reasoning tokens (when ``think``) arrive in the
        message's ``thinking`` field, answer tokens in ``content`` - yielded as distinct chunks."""
        options: dict[str, object] = {"temperature": 0}
        if self._num_ctx is not None:
            options["num_ctx"] = self._num_ctx
        if self._num_predict is not None:
            options["num_predict"] = self._num_predict
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "think": think,
            "options": options,
        }
        if self._keep_alive is not None:
            payload["keep_alive"] = self._keep_alive
        with httpx.stream(
            "POST", f"{self._base_url}/api/chat", json=payload, timeout=self._timeout
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                message = json.loads(line).get("message") or {}
                reasoning = message.get("thinking")
                if reasoning:
                    yield ChatChunk(kind="reasoning", text=reasoning)
                content = message.get("content")
                if content:
                    yield ChatChunk(kind="answer", text=content)
