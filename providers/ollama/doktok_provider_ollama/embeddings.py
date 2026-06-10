"""Text embeddings via a local Ollama model (DOKTOK_EMBEDDING_MODEL, default mxbai-embed-large).

Talks only to the local Ollama endpoint (no external egress). mxbai-embed-large and bge-m3 are
1024-dimensional, matching the pgvector column.
"""

from __future__ import annotations

import httpx


class OllamaEmbeddingProvider:
    """``EmbeddingProvider`` backed by Ollama's ``/api/embed`` batch endpoint."""

    def __init__(self, model: str, base_url: str, *, timeout: float = 120.0) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = httpx.post(
            f"{self._base_url}/api/embed",
            json={"model": self._model, "input": texts},
            timeout=self._timeout,
        )
        response.raise_for_status()
        embeddings: list[list[float]] = response.json()["embeddings"]
        return embeddings
