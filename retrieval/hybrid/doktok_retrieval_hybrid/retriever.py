"""Hybrid retriever: pgvector semantic search + Postgres full-text search (ADR-0005).

Runs both signals, fuses them with Reciprocal Rank Fusion (RRF), and returns tenant-scoped hits
joined with their document for display. Never vector-only.
"""

from __future__ import annotations

from typing import Any

from doktok_contracts.ports import EmbeddingProvider
from doktok_contracts.schemas import SearchHit
from doktok_storage_postgres import Database
from doktok_storage_postgres.repositories import to_vector_literal
from psycopg.rows import dict_row

_RRF_K = 60  # standard reciprocal-rank-fusion constant
_SNIPPET_CHARS = 240

_SELECT = (
    "SELECT c.id AS chunk_id, c.document_id, c.page_start, c.page_end, c.text, "
    "d.original_filename, d.title"
)


def _snippet(text: str) -> str:
    text = " ".join(text.split())
    return text[:_SNIPPET_CHARS] + ("..." if len(text) > _SNIPPET_CHARS else "")


class HybridPostgresRetriever:
    def __init__(self, db: Database, embedding_provider: EmbeddingProvider) -> None:
        self._db = db
        self._embeddings = embedding_provider

    def search(self, tenant_id: str, query: str, limit: int = 10) -> list[SearchHit]:
        query = query.strip()
        if not query:
            return []
        candidates = max(limit * 4, 20)
        query_vec = to_vector_literal(self._embeddings.embed([query])[0])

        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            vector_rows = cur.execute(
                f"{_SELECT}, (c.embedding <=> %s::vector) AS distance "
                "FROM document_chunks c "
                "JOIN documents d ON d.id = c.document_id AND d.tenant_id = c.tenant_id "
                "WHERE c.tenant_id = %s AND c.embedding IS NOT NULL "
                "ORDER BY c.embedding <=> %s::vector LIMIT %s",
                (query_vec, tenant_id, query_vec, candidates),
            ).fetchall()
            text_rows = cur.execute(
                f"{_SELECT}, ts_rank(c.tsv, plainto_tsquery('english', %s)) AS rank "
                "FROM document_chunks c "
                "JOIN documents d ON d.id = c.document_id AND d.tenant_id = c.tenant_id "
                "WHERE c.tenant_id = %s AND c.tsv @@ plainto_tsquery('english', %s) "
                "ORDER BY rank DESC LIMIT %s",
                (query, tenant_id, query, candidates),
            ).fetchall()

        return _fuse(vector_rows, text_rows, limit)


def _fuse(
    vector_rows: list[dict[str, Any]], text_rows: list[dict[str, Any]], limit: int
) -> list[SearchHit]:
    fused: dict[str, dict[str, Any]] = {}

    for rank, row in enumerate(vector_rows, start=1):
        entry = fused.setdefault(row["chunk_id"], {"row": row, "score": 0.0})
        entry["score"] += 1.0 / (_RRF_K + rank)
        entry["vector_score"] = 1.0 - float(row["distance"])  # cosine similarity

    for rank, row in enumerate(text_rows, start=1):
        entry = fused.setdefault(row["chunk_id"], {"row": row, "score": 0.0})
        entry["score"] += 1.0 / (_RRF_K + rank)
        entry["text_score"] = float(row["rank"])

    ordered = sorted(fused.values(), key=lambda e: e["score"], reverse=True)[:limit]
    hits: list[SearchHit] = []
    for entry in ordered:
        row = entry["row"]
        hits.append(
            SearchHit(
                document_id=row["document_id"],
                chunk_id=row["chunk_id"],
                original_filename=row["original_filename"],
                title=row["title"],
                page_start=row["page_start"],
                page_end=row["page_end"],
                snippet=_snippet(row["text"]),
                score=round(entry["score"], 6),
                vector_score=entry.get("vector_score"),
                text_score=entry.get("text_score"),
            )
        )
    return hits
