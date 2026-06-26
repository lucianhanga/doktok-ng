"""Text embeddings via a local Ollama model (DOKTOK_EMBEDDING_MODEL, default qwen3-embedding:0.6b).

Talks only to the local Ollama endpoint (no external egress). qwen3-embedding:0.6b is 1024-
dimensional, matching the pgvector column, and (unlike the former mxbai-embed-large) does not
truncate longer chunks at 512 tokens.
"""

from __future__ import annotations

import httpx
from doktok_contracts.media import LlmUsage

from doktok_provider_ollama.usage import usage_from_embed


class OllamaEmbeddingProvider:
    """``EmbeddingProvider`` backed by Ollama's ``/api/embed`` batch endpoint."""

    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        timeout: float = 600.0,
        keep_alive: str | None = None,
        num_ctx: int | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._keep_alive = keep_alive
        # Cap the context to the chunk size: the model's large default (e.g. 32k) otherwise
        # allocates a needless KV cache. Chunks fit well inside this, so embeddings are unchanged.
        self._num_ctx = num_ctx
        # Token usage of the most recent embed() call (read by the reconciler via the processor).
        self._last_usage: LlmUsage | None = None

    @property
    def model(self) -> str:
        return self._model

    def get_last_usage(self) -> LlmUsage | None:
        return self._last_usage

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._last_usage = None
        if not texts:
            return []
        payload: dict[str, object] = {"model": self._model, "input": texts}
        if self._num_ctx is not None:
            payload["options"] = {"num_ctx": self._num_ctx}
        if self._keep_alive is not None:
            # Pin the embedding model resident so it is not evicted and then unable to reload while
            # the large chat model is pinned (which would hang the call and stall the reconciler).
            payload["keep_alive"] = self._keep_alive
        response = httpx.post(
            f"{self._base_url}/api/embed",
            json=payload,
            timeout=self._timeout,
        )
        response.raise_for_status()
        body = response.json()
        self._last_usage = usage_from_embed(body)
        embeddings: list[list[float]] = body["embeddings"]
        return embeddings
