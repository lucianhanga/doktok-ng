"""In-memory chunk repository for tests and local/dev runs (tenant-scoped)."""

from __future__ import annotations

import math

from doktok_contracts.schemas import DocumentChunk, SimilarDocument


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


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

    def read_texts(self, tenant_id: str, chunk_ids: list[str]) -> dict[str, str]:
        wanted = set(chunk_ids)
        return {c.id: c.text for c in self.chunks if c.tenant_id == tenant_id and c.id in wanted}

    def chunk_counts_for_documents(self, tenant_id: str, document_ids: list[str]) -> dict[str, int]:
        wanted = set(document_ids)
        counts: dict[str, int] = {}
        for c in self.chunks:
            if c.tenant_id != tenant_id or c.document_id not in wanted:
                continue
            counts[c.document_id] = counts.get(c.document_id, 0) + 1
        return counts

    def similar_documents(
        self, tenant_id: str, document_id: str, *, limit: int = 6
    ) -> list[SimilarDocument]:
        """Same ranking contract as the Postgres pgvector query, computed naively in Python
        (#730): per candidate chunk the best cosine similarity against any source chunk, averaged
        per candidate document. Title/filename are the endpoint's enrichment, not the store's."""
        src = [
            e
            for c, e in zip(self.chunks, self.embeddings, strict=True)
            if c.tenant_id == tenant_id and c.document_id == document_id
        ]
        if not src:
            return []
        best: dict[str, list[float]] = {}
        for c, e in zip(self.chunks, self.embeddings, strict=True):
            if c.tenant_id != tenant_id or c.document_id == document_id:
                continue
            best.setdefault(c.document_id, []).append(max(_cosine(e, s) for s in src))
        ranked = sorted(
            ((doc_id, sum(sims) / len(sims)) for doc_id, sims in best.items()),
            key=lambda kv: kv[1],
            reverse=True,
        )[:limit]
        return [
            SimilarDocument(document_id=doc_id, original_filename=doc_id, score=score)
            for doc_id, score in ranked
        ]
