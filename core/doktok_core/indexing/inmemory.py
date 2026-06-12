"""In-memory chunk repository for tests and local/dev runs (tenant-scoped)."""

from __future__ import annotations

from doktok_contracts.schemas import DocumentChunk


class InMemoryChunkRepository:
    def __init__(self) -> None:
        self.chunks: list[DocumentChunk] = []
        self.embeddings: list[list[float]] = []

    def add_chunks(self, chunks: list[DocumentChunk], embeddings: list[list[float]]) -> None:
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            self.chunks.append(chunk.model_copy(deep=True))
            self.embeddings.append(list(embedding))

    def delete_for_document(self, tenant_id: str, document_id: str) -> None:
        kept = [
            (c, e)
            for c, e in zip(self.chunks, self.embeddings, strict=True)
            if not (c.tenant_id == tenant_id and c.document_id == document_id)
        ]
        self.chunks = [c for c, _ in kept]
        self.embeddings = [e for _, e in kept]

    def read_embeddings(self, tenant_id: str, limit: int) -> list[tuple[str, str, list[float]]]:
        rows = [
            (c.id, c.document_id, list(e))
            for c, e in zip(self.chunks, self.embeddings, strict=True)
            if c.tenant_id == tenant_id
        ]
        rows.sort(key=lambda r: r[0])  # deterministic order so a truncated read is stable
        return rows[:limit]

    def embedding_fingerprint(self, tenant_id: str) -> str:
        ids = sorted(c.id for c in self.chunks if c.tenant_id == tenant_id)
        return f"chunks={len(ids)};latest={ids[-1] if ids else ''}"
