"""Text embeddings via a local Ollama model (DOKTOK_EMBEDDING_MODEL, default qwen3-embedding:0.6b).

Talks only to the local Ollama endpoint (no external egress). qwen3-embedding:0.6b is 1024-
dimensional, matching the pgvector column, and (unlike the former mxbai-embed-large) does not
truncate longer chunks at 512 tokens.
"""

from __future__ import annotations

import httpx


class OllamaEmbeddingProvider:
    """``EmbeddingProvider`` backed by Ollama's ``/api/embed`` batch endpoint."""

    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        timeout: float = 600.0,
        keep_alive: str | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._keep_alive = keep_alive

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, object] = {"model": self._model, "input": texts}
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
        embeddings: list[list[float]] = response.json()["embeddings"]
        return embeddings
