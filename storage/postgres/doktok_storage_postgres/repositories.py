"""PostgreSQL repository adapters. All reads are scoped by tenant_id (ADR-0007)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from doktok_contracts.media import ExtractedTerm
from doktok_contracts.schemas import (
    AuditEvent,
    Category,
    CategorySummary,
    Document,
    DocumentChunk,
    DocumentEntity,
    DocumentFeature,
    DocumentStatus,
    EntitySummary,
    EntityType,
    ExtractedRecord,
    FeatureStatus,
    IngestionJob,
    JobStatus,
    StatsSummary,
    TokenSuggestion,
)
from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg.types.json import Json

from doktok_storage_postgres.db import Database


def to_vector_literal(values: list[float]) -> str:
    """Format a float vector as a pgvector literal, e.g. ``[0.1,0.2,0.3]``."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


_DOC_COLUMNS = (
    "id, tenant_id, current_version_id, sha256, original_filename, detected_mime, "
    "title, status, storage_path, created_at, activated_at, duplicate_of, metadata, "
    "ingested_at, document_date, location, summary"
)
_DOC_COLUMNS_D = ", ".join(f"d.{c}" for c in _DOC_COLUMNS.split(", "))


def _row_to_document(row: dict[str, Any]) -> Document:
    return Document(
        id=row["id"],
        tenant_id=row["tenant_id"],
        current_version_id=row["current_version_id"],
        sha256=row["sha256"],
        original_filename=row["original_filename"],
        detected_mime=row["detected_mime"],
        title=row["title"],
        status=DocumentStatus(row["status"]),
        storage_path=row["storage_path"],
        created_at=row["created_at"],
        activated_at=row["activated_at"],
        duplicate_of=row["duplicate_of"],
        metadata=row["metadata"] or {},
        ingested_at=row["ingested_at"],
        document_date=row["document_date"],
        location=row["location"],
        summary=row["summary"],
    )


_COLUMNS = (
    "id, tenant_id, document_id, source_path, status, detected_mime, sha256, "
    "error_code, error_message, started_at, finished_at, metadata"
)


def _row_to_job(row: dict[str, Any]) -> IngestionJob:
    return IngestionJob(
        id=row["id"],
        tenant_id=row["tenant_id"],
        document_id=row["document_id"],
        source_path=row["source_path"],
        status=JobStatus(row["status"]),
        detected_mime=row["detected_mime"],
        sha256=row["sha256"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        metadata=row["metadata"] or {},
    )


class PostgresIngestionJobRepository:
    """``IngestionJobRepository`` backed by PostgreSQL. Uses parameterized, tenant-scoped SQL."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, job: IngestionJob) -> None:
        with self._db.connection() as conn:
            conn.execute(
                f"INSERT INTO ingestion_jobs ({_COLUMNS}) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    job.id,
                    job.tenant_id,
                    job.document_id,
                    job.source_path,
                    job.status.value,
                    job.detected_mime,
                    job.sha256,
                    job.error_code,
                    job.error_message,
                    job.started_at,
                    job.finished_at,
                    Json(job.metadata),
                ),
            )

    def update(self, job: IngestionJob) -> None:
        # Scope the UPDATE by tenant so a job can never be moved across tenants.
        with self._db.connection() as conn:
            conn.execute(
                "UPDATE ingestion_jobs SET document_id=%s, source_path=%s, status=%s, "
                "detected_mime=%s, sha256=%s, error_code=%s, error_message=%s, "
                "started_at=%s, finished_at=%s, metadata=%s WHERE id=%s AND tenant_id=%s",
                (
                    job.document_id,
                    job.source_path,
                    job.status.value,
                    job.detected_mime,
                    job.sha256,
                    job.error_code,
                    job.error_message,
                    job.started_at,
                    job.finished_at,
                    Json(job.metadata),
                    job.id,
                    job.tenant_id,
                ),
            )

    def get(self, tenant_id: str, job_id: str) -> IngestionJob | None:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            row = cur.execute(
                f"SELECT {_COLUMNS} FROM ingestion_jobs WHERE id=%s AND tenant_id=%s",
                (job_id, tenant_id),
            ).fetchone()
        return _row_to_job(row) if row else None

    def list_jobs(self, tenant_id: str, limit: int = 50, offset: int = 0) -> list[IngestionJob]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_COLUMNS} FROM ingestion_jobs WHERE tenant_id=%s "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (tenant_id, limit, offset),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def find_by_sha256(self, tenant_id: str, sha256: str) -> list[IngestionJob]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_COLUMNS} FROM ingestion_jobs WHERE tenant_id=%s AND sha256=%s "
                "ORDER BY created_at DESC",
                (tenant_id, sha256),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def delete_failed_for_sha(self, tenant_id: str, sha256: str) -> int:
        with self._db.connection() as conn:
            cur = conn.execute(
                "DELETE FROM ingestion_jobs WHERE tenant_id=%s AND sha256=%s AND status='failed'",
                (tenant_id, sha256),
            )
            return cur.rowcount


class PostgresDocumentRepository:
    """``DocumentRepository`` backed by PostgreSQL. Tenant-scoped reads."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, document: Document) -> None:
        with self._db.connection() as conn:
            conn.execute(
                f"INSERT INTO documents ({_DOC_COLUMNS}) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    document.id,
                    document.tenant_id,
                    document.current_version_id,
                    document.sha256,
                    document.original_filename,
                    document.detected_mime,
                    document.title,
                    document.status.value,
                    document.storage_path,
                    document.created_at,
                    document.activated_at,
                    document.duplicate_of,
                    Json(document.metadata),
                    document.ingested_at,
                    document.document_date,
                    document.location,
                    document.summary,
                ),
            )

    def set_metadata(
        self,
        tenant_id: str,
        document_id: str,
        *,
        title: str | None,
        document_date: date | None,
        location: str | None,
        summary: str | None,
    ) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "UPDATE documents SET title=COALESCE(%s, title), document_date=%s, location=%s, "
                "summary=%s WHERE id=%s AND tenant_id=%s",
                (title, document_date, location, summary, document_id, tenant_id),
            )

    def get(self, tenant_id: str, document_id: str) -> Document | None:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            row = cur.execute(
                f"SELECT {_DOC_COLUMNS} FROM documents WHERE id=%s AND tenant_id=%s",
                (document_id, tenant_id),
            ).fetchone()
        return _row_to_document(row) if row else None

    def list_documents(self, tenant_id: str, limit: int = 50, offset: int = 0) -> list[Document]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_DOC_COLUMNS} FROM documents WHERE tenant_id=%s "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (tenant_id, limit, offset),
            ).fetchall()
        return [_row_to_document(row) for row in rows]

    def delete(self, tenant_id: str, document_id: str) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "DELETE FROM documents WHERE id=%s AND tenant_id=%s",
                (document_id, tenant_id),
            )


_AUDIT_COLUMNS = "id, tenant_id, event_type, actor, document_id, job_id, timestamp, metadata"


def _row_to_event(row: dict[str, Any]) -> AuditEvent:
    return AuditEvent(
        id=row["id"],
        tenant_id=row["tenant_id"],
        event_type=row["event_type"],
        actor=row["actor"],
        document_id=row["document_id"],
        job_id=row["job_id"],
        timestamp=row["timestamp"],
        metadata=row["metadata"] or {},
    )


class PostgresAuditLogRepository:
    """``AuditLogRepository`` backed by PostgreSQL. Append-only: record + tenant-scoped reads."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, event: AuditEvent) -> None:
        with self._db.connection() as conn:
            conn.execute(
                f"INSERT INTO audit_events ({_AUDIT_COLUMNS}) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    event.id,
                    event.tenant_id,
                    event.event_type,
                    event.actor,
                    event.document_id,
                    event.job_id,
                    event.timestamp,
                    Json(event.metadata),
                ),
            )

    def list_events(
        self,
        tenant_id: str,
        *,
        document_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]:
        clause = "WHERE tenant_id=%s"
        params: list[object] = [tenant_id]
        if document_id is not None:
            clause += " AND document_id=%s"
            params.append(document_id)
        params.extend([limit, offset])
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_AUDIT_COLUMNS} FROM audit_events {clause} "
                "ORDER BY timestamp DESC, id DESC LIMIT %s OFFSET %s",
                tuple(params),
            ).fetchall()
        return [_row_to_event(row) for row in rows]


class PostgresChunkRepository:
    """``ChunkRepository`` storing chunks with their embedding and a generated FTS vector."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add_chunks(self, chunks: list[DocumentChunk], embeddings: list[list[float]]) -> None:
        if not chunks:
            return
        with self._db.connection() as conn:
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                conn.execute(
                    "INSERT INTO document_chunks "
                    "(id, tenant_id, document_id, version_id, page_start, page_end, "
                    "heading_path, text, token_count, embedding, metadata) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)",
                    (
                        chunk.id,
                        chunk.tenant_id,
                        chunk.document_id,
                        chunk.version_id,
                        chunk.page_start,
                        chunk.page_end,
                        Json(chunk.heading_path),
                        chunk.text,
                        chunk.token_count,
                        to_vector_literal(embedding),
                        Json(chunk.metadata),
                    ),
                )

    def delete_for_document(self, tenant_id: str, document_id: str) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "DELETE FROM document_chunks WHERE tenant_id=%s AND document_id=%s",
                (tenant_id, document_id),
            )


class PostgresEntityRepository:
    """``EntityRepository`` backed by PostgreSQL. Tenant-scoped."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add_entities(self, entities: list[DocumentEntity]) -> None:
        if not entities:
            return
        with self._db.connection() as conn:
            for entity in entities:
                conn.execute(
                    "INSERT INTO document_entities "
                    "(id, tenant_id, document_id, version_id, chunk_id, entity_text, "
                    "entity_type, normalized_value, frequency, metadata) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        entity.id,
                        entity.tenant_id,
                        entity.document_id,
                        entity.version_id,
                        entity.chunk_id,
                        entity.entity_text,
                        entity.entity_type.value,
                        entity.normalized_value,
                        entity.frequency,
                        Json(entity.metadata),
                    ),
                )

    def delete_for_document(self, tenant_id: str, document_id: str) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "DELETE FROM document_entities WHERE tenant_id=%s AND document_id=%s",
                (tenant_id, document_id),
            )

    def list_distinct(
        self,
        tenant_id: str,
        *,
        entity_type: EntityType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EntitySummary]:
        clause = "WHERE tenant_id=%s"
        params: list[object] = [tenant_id]
        if entity_type is not None:
            clause += " AND entity_type=%s"
            params.append(entity_type.value)
        params.extend([limit, offset])
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                "SELECT entity_type, normalized_value, "
                "COUNT(DISTINCT document_id) AS document_count, "
                "COALESCE(SUM(frequency), 0) AS occurrences "
                f"FROM document_entities {clause} "
                "GROUP BY entity_type, normalized_value "
                "ORDER BY occurrences DESC, normalized_value ASC LIMIT %s OFFSET %s",
                tuple(params),
            ).fetchall()
        return [
            EntitySummary(
                entity_type=EntityType(row["entity_type"]),
                normalized_value=row["normalized_value"],
                document_count=int(row["document_count"]),
                occurrences=int(row["occurrences"]),
            )
            for row in rows
        ]

    def documents_for_entity(
        self,
        tenant_id: str,
        entity_type: EntityType,
        normalized_value: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT DISTINCT {_DOC_COLUMNS_D} FROM documents d "
                "JOIN document_entities e ON e.document_id = d.id AND e.tenant_id = d.tenant_id "
                "WHERE d.tenant_id=%s AND e.entity_type=%s AND e.normalized_value=%s "
                "ORDER BY d.created_at DESC LIMIT %s OFFSET %s",
                (tenant_id, entity_type.value, normalized_value, limit, offset),
            ).fetchall()
        return [_row_to_document(row) for row in rows]

    def list_for_document(self, tenant_id: str, document_id: str) -> list[DocumentEntity]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                "SELECT id, tenant_id, document_id, version_id, chunk_id, entity_text, "
                "entity_type, normalized_value, frequency, metadata "
                "FROM document_entities WHERE tenant_id=%s AND document_id=%s "
                "ORDER BY frequency DESC, normalized_value ASC",
                (tenant_id, document_id),
            ).fetchall()
        return [
            DocumentEntity(
                id=row["id"],
                tenant_id=row["tenant_id"],
                document_id=row["document_id"],
                version_id=row["version_id"] or "",
                chunk_id=row["chunk_id"],
                entity_text=row["entity_text"],
                entity_type=EntityType(row["entity_type"]),
                normalized_value=row["normalized_value"],
                frequency=row["frequency"],
                metadata=row["metadata"] or {},
            )
            for row in rows
        ]

    def suggest_tokens(
        self,
        tenant_id: str,
        prefix: str,
        *,
        selected: list[str] | None = None,
        limit: int = 10,
    ) -> list[TokenSuggestion]:
        like = _like_prefix(prefix)
        selected_lower = [s.lower() for s in (selected or [])]
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            if selected_lower:
                rows = cur.execute(
                    "WITH matching AS ("
                    "  SELECT document_id FROM document_entities"
                    "  WHERE tenant_id=%s AND lower(normalized_value) = ANY(%s)"
                    "  GROUP BY document_id"
                    "  HAVING COUNT(DISTINCT lower(normalized_value)) = %s) "
                    "SELECT normalized_value AS value, COUNT(DISTINCT document_id) AS dc "
                    "FROM document_entities "
                    "WHERE tenant_id=%s AND document_id IN (SELECT document_id FROM matching) "
                    "AND normalized_value ILIKE %s AND lower(normalized_value) <> ALL(%s) "
                    "GROUP BY normalized_value ORDER BY dc DESC, value ASC LIMIT %s",
                    (
                        tenant_id,
                        selected_lower,
                        len(selected_lower),
                        tenant_id,
                        like,
                        selected_lower,
                        limit,
                    ),
                ).fetchall()
            else:
                rows = cur.execute(
                    "SELECT normalized_value AS value, COUNT(DISTINCT document_id) AS dc "
                    "FROM document_entities WHERE tenant_id=%s AND normalized_value ILIKE %s "
                    "GROUP BY normalized_value ORDER BY dc DESC, value ASC LIMIT %s",
                    (tenant_id, like, limit),
                ).fetchall()
        return [TokenSuggestion(value=r["value"], document_count=int(r["dc"])) for r in rows]

    def documents_for_tokens(
        self,
        tenant_id: str,
        tokens: list[str],
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]:
        tokens_lower = [t.lower() for t in tokens]
        if not tokens_lower:
            return []
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_DOC_COLUMNS} FROM documents WHERE tenant_id=%s AND id IN ("
                "  SELECT document_id FROM document_entities"
                "  WHERE tenant_id=%s AND lower(normalized_value) = ANY(%s)"
                "  GROUP BY document_id"
                "  HAVING COUNT(DISTINCT lower(normalized_value)) = %s) "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (tenant_id, tenant_id, tokens_lower, len(tokens_lower), limit, offset),
            ).fetchall()
        return [_row_to_document(row) for row in rows]


def _like_prefix(prefix: str) -> str:
    """Escape LIKE wildcards in ``prefix`` and append ``%`` for a case-insensitive prefix match."""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


class PostgresStatsRepository:
    """``StatsRepository`` computing at-a-glance tenant counts."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def _scalar(self, cur: Any, sql: str, tenant_id: str) -> int:
        row = cur.execute(sql, (tenant_id,)).fetchone()
        return int(row["n"]) if row else 0

    def summary(self, tenant_id: str) -> StatsSummary:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            documents = self._scalar(
                cur, "SELECT COUNT(*) AS n FROM documents WHERE tenant_id=%s", tenant_id
            )
            job_rows = cur.execute(
                "SELECT status, COUNT(*) AS n FROM ingestion_jobs WHERE tenant_id=%s "
                "GROUP BY status",
                (tenant_id,),
            ).fetchall()
            entities = self._scalar(
                cur,
                "SELECT COUNT(DISTINCT (entity_type, normalized_value)) AS n "
                "FROM document_entities WHERE tenant_id=%s",
                tenant_id,
            )
            pending_feature_docs = self._scalar(
                cur,
                "SELECT COUNT(DISTINCT document_id) AS n FROM document_features "
                "WHERE tenant_id=%s AND status <> 'done'",
                tenant_id,
            )
        return StatsSummary(
            documents=documents,
            jobs={row["status"]: int(row["n"]) for row in job_rows},
            entities=entities,
            documents_pending_features=pending_feature_docs,
        )


class PostgresLexicalTermExtractor:
    """``LexicalTermExtractor`` using PostgreSQL full-text lexemes (stopwords removed, stemmed).

    ``to_tsvector(config, text)`` normalizes the document into significant lexemes for the given
    language config; ``unnest`` exposes each lexeme with its positions so we can rank by frequency.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def extract_terms(
        self, text: str, *, config: str = "simple", limit: int = 200
    ) -> list[ExtractedTerm]:
        if not text.strip():
            return []
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                "SELECT lexeme, COALESCE(array_length(positions, 1), 1) AS freq "
                "FROM unnest(to_tsvector(%s::regconfig, %s)) "
                "WHERE length(lexeme) >= 2 AND lexeme ~ '[[:alnum:]]' "
                "ORDER BY freq DESC, lexeme ASC LIMIT %s",
                (config, text, limit),
            ).fetchall()
        return [ExtractedTerm(term=row["lexeme"], frequency=int(row["freq"])) for row in rows]


_FEATURE_COLUMNS = (
    "id, tenant_id, document_id, feature, feature_version, status, attempts, max_attempts, "
    "last_error, last_attempt_at, completed_at, next_attempt_at, created_at, updated_at"
)
_FEATURE_COLUMNS_F = ", ".join(f"f.{c}" for c in _FEATURE_COLUMNS.split(", "))


def _row_to_feature(row: dict[str, Any]) -> DocumentFeature:
    return DocumentFeature(
        id=row["id"],
        tenant_id=row["tenant_id"],
        document_id=row["document_id"],
        feature=row["feature"],
        feature_version=row["feature_version"],
        status=FeatureStatus(row["status"]),
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        last_error=row["last_error"],
        last_attempt_at=row["last_attempt_at"],
        completed_at=row["completed_at"],
        next_attempt_at=row["next_attempt_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class PostgresFeatureRepository:
    """The document_features ledger (ADR-0009). Claiming is multi-worker safe via SKIP LOCKED."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def record_done(
        self, tenant_id: str, document_id: str, feature: str, feature_version: int
    ) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "INSERT INTO document_features "
                "(id, tenant_id, document_id, feature, feature_version, status, completed_at) "
                "VALUES (%s, %s, %s, %s, %s, 'done', now()) "
                "ON CONFLICT (tenant_id, document_id, feature) DO UPDATE SET "
                "status='done', feature_version=EXCLUDED.feature_version, completed_at=now(), "
                "last_error=NULL, attempts=0, next_attempt_at=NULL, updated_at=now()",
                (uuid.uuid4().hex, tenant_id, document_id, feature, feature_version),
            )

    def ensure_for_active(self, tenant_id: str, features: list[tuple[str, int]]) -> int:
        affected = 0
        with self._db.connection() as conn:
            for name, version in features:
                cur = conn.execute(
                    "INSERT INTO document_features "
                    "(id, tenant_id, document_id, feature, feature_version, status) "
                    "SELECT gen_random_uuid()::text, d.tenant_id, d.id, %s, %s, 'pending' "
                    "FROM documents d WHERE d.tenant_id=%s AND d.status='active' "
                    "AND NOT EXISTS (SELECT 1 FROM document_features f "
                    "WHERE f.tenant_id=d.tenant_id AND f.document_id=d.id AND f.feature=%s)",
                    (name, version, tenant_id, name),
                )
                affected += cur.rowcount
                # Version bump: reprocess documents whose completed feature is now stale.
                cur = conn.execute(
                    "UPDATE document_features SET status='pending', feature_version=%s, "
                    "attempts=0, last_error=NULL, next_attempt_at=NULL, updated_at=now() "
                    "WHERE tenant_id=%s AND feature=%s AND status='done' AND feature_version < %s",
                    (version, tenant_id, name, version),
                )
                affected += cur.rowcount
        return affected

    def claim_next(
        self, tenant_id: str, *, now: datetime, reclaim_before: datetime
    ) -> DocumentFeature | None:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            row = cur.execute(
                "WITH due AS ("
                "  SELECT id FROM document_features WHERE tenant_id=%s AND ("
                "    status='pending'"
                "    OR (status='failed' AND attempts < max_attempts "
                "        AND (next_attempt_at IS NULL OR next_attempt_at <= %s))"
                "    OR (status='running' AND last_attempt_at < %s))"
                "  ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) "
                "UPDATE document_features f SET status='running', attempts=f.attempts+1, "
                "last_attempt_at=%s, updated_at=now() FROM due WHERE f.id=due.id "
                f"RETURNING {_FEATURE_COLUMNS_F}",
                (tenant_id, now, reclaim_before, now),
            ).fetchone()
        return _row_to_feature(row) if row else None

    def mark_done(self, feature_id: str, *, feature_version: int) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "UPDATE document_features SET status='done', feature_version=%s, "
                "completed_at=now(), last_error=NULL, updated_at=now() WHERE id=%s",
                (feature_version, feature_id),
            )

    def mark_failed(self, feature_id: str, *, error: str, next_attempt_at: datetime) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "UPDATE document_features SET status='failed', last_error=%s, "
                "next_attempt_at=%s, updated_at=now() WHERE id=%s",
                (error[:2000], next_attempt_at, feature_id),
            )

    def list_for_document(self, tenant_id: str, document_id: str) -> list[DocumentFeature]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_FEATURE_COLUMNS} FROM document_features "
                "WHERE tenant_id=%s AND document_id=%s ORDER BY feature",
                (tenant_id, document_id),
            ).fetchall()
        return [_row_to_feature(row) for row in rows]

    def list_for_tenant(self, tenant_id: str, *, limit: int = 2000) -> list[DocumentFeature]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_FEATURE_COLUMNS} FROM document_features WHERE tenant_id=%s "
                "ORDER BY document_id, feature LIMIT %s",
                (tenant_id, limit),
            ).fetchall()
        return [_row_to_feature(row) for row in rows]

    def reset(self, tenant_id: str, document_id: str, feature: str) -> bool:
        with self._db.connection() as conn:
            cur = conn.execute(
                "UPDATE document_features SET status='pending', attempts=0, last_error=NULL, "
                "next_attempt_at=NULL, updated_at=now() "
                "WHERE tenant_id=%s AND document_id=%s AND feature=%s",
                (tenant_id, document_id, feature),
            )
            return cur.rowcount > 0


_CAT_COLUMNS = "id, tenant_id, name, normalized, status, created_at"


def _row_to_category(row: dict[str, Any]) -> Category:
    return Category(
        id=row["id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        normalized=row["normalized"],
        status=row["status"],
        created_at=row["created_at"],
    )


class PostgresCategoryRepository:
    """``CategoryRepository`` backed by PostgreSQL. Tenant-scoped; caps enforced by DB triggers."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def _one(self, sql: str, params: tuple[Any, ...]) -> Category | None:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            row = cur.execute(sql, params).fetchone()
        return _row_to_category(row) if row else None

    def list_active(self, tenant_id: str) -> list[Category]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_CAT_COLUMNS} FROM categories "
                "WHERE tenant_id=%s AND status='active' ORDER BY name",
                (tenant_id,),
            ).fetchall()
        return [_row_to_category(r) for r in rows]

    def find_by_normalized(self, tenant_id: str, normalized: str) -> Category | None:
        return self._one(
            f"SELECT {_CAT_COLUMNS} FROM categories "
            "WHERE tenant_id=%s AND normalized=%s AND status='active'",
            (tenant_id, normalized),
        )

    def find_similar(
        self, tenant_id: str, normalized: str, *, threshold: float = 0.55
    ) -> Category | None:
        return self._one(
            f"SELECT {_CAT_COLUMNS} FROM categories "
            "WHERE tenant_id=%s AND status='active' AND similarity(normalized, %s) >= %s "
            "ORDER BY similarity(normalized, %s) DESC LIMIT 1",
            (tenant_id, normalized, threshold, normalized),
        )

    def find_nearest(self, tenant_id: str, normalized: str) -> Category | None:
        return self._one(
            f"SELECT {_CAT_COLUMNS} FROM categories WHERE tenant_id=%s AND status='active' "
            "ORDER BY similarity(normalized, %s) DESC LIMIT 1",
            (tenant_id, normalized),
        )

    def active_count(self, tenant_id: str) -> int:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            row = cur.execute(
                "SELECT count(*) AS n FROM categories WHERE tenant_id=%s AND status='active'",
                (tenant_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def create(self, tenant_id: str, name: str, normalized: str) -> Category | None:
        category_id = uuid.uuid4().hex
        try:
            with self._db.connection() as conn:
                cur = conn.cursor(row_factory=dict_row)
                row = cur.execute(
                    f"INSERT INTO categories (id, tenant_id, name, normalized, status) "
                    f"VALUES (%s, %s, %s, %s, 'active') "
                    f"ON CONFLICT (tenant_id, normalized) DO NOTHING RETURNING {_CAT_COLUMNS}",
                    (category_id, tenant_id, name, normalized),
                ).fetchone()
        except pg_errors.CheckViolation:
            return None  # tenant hit the 20-category cap (rare race)
        if row:
            return _row_to_category(row)
        return self.find_by_normalized(tenant_id, normalized)  # lost the create race -> existing

    def set_document_categories(
        self, tenant_id: str, document_id: str, category_ids: list[str]
    ) -> None:
        with self._db.connection() as conn, conn.transaction():
            conn.execute(
                "DELETE FROM document_category_links WHERE tenant_id=%s AND document_id=%s",
                (tenant_id, document_id),
            )
            for category_id in category_ids:
                conn.execute(
                    "INSERT INTO document_category_links (tenant_id, document_id, category_id) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (tenant_id, document_id, category_id),
                )

    def list_for_document(self, tenant_id: str, document_id: str) -> list[Category]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {', '.join('c.' + c for c in _CAT_COLUMNS.split(', '))} FROM categories c "
                "JOIN document_category_links l ON l.category_id = c.id "
                "WHERE l.tenant_id=%s AND l.document_id=%s ORDER BY c.name",
                (tenant_id, document_id),
            ).fetchall()
        return [_row_to_category(r) for r in rows]

    def list_summary(self, tenant_id: str) -> list[CategorySummary]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                "SELECT c.name AS name, count(l.document_id) AS dc FROM categories c "
                "LEFT JOIN document_category_links l "
                "ON l.category_id = c.id AND l.tenant_id = c.tenant_id "
                "WHERE c.tenant_id=%s AND c.status='active' "
                "GROUP BY c.name ORDER BY dc DESC, c.name",
                (tenant_id,),
            ).fetchall()
        return [CategorySummary(name=r["name"], document_count=int(r["dc"])) for r in rows]

    def documents_for_category(
        self, tenant_id: str, name: str, *, limit: int = 50, offset: int = 0
    ) -> list[Document]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_DOC_COLUMNS_D} FROM documents d "
                "JOIN document_category_links l "
                "ON l.document_id = d.id AND l.tenant_id = d.tenant_id "
                "JOIN categories c ON c.id = l.category_id "
                "WHERE d.tenant_id=%s AND c.name=%s AND c.status='active' "
                "ORDER BY d.created_at DESC LIMIT %s OFFSET %s",
                (tenant_id, name, limit, offset),
            ).fetchall()
        return [_row_to_document(row) for row in rows]


_REC_COLUMNS = (
    "id, tenant_id, document_id, record_type, source_page, raw_text, occurred_on, amount_minor, "
    "currency, direction, merchant_raw, merchant_normalized, description, account_label, confidence"
)


def _row_to_record(row: dict[str, Any]) -> ExtractedRecord:
    return ExtractedRecord(
        id=row["id"],
        tenant_id=row["tenant_id"],
        document_id=row["document_id"],
        record_type=row["record_type"],
        source_page=row["source_page"],
        raw_text=row["raw_text"],
        occurred_on=row["occurred_on"],
        amount_minor=row["amount_minor"],
        currency=row["currency"].strip() if row["currency"] else None,
        direction=row["direction"],
        merchant_raw=row["merchant_raw"],
        merchant_normalized=row["merchant_normalized"],
        description=row["description"],
        account_label=row["account_label"],
        confidence=row["confidence"],
    )


class PostgresRecordRepository:
    """``RecordRepository`` backed by PostgreSQL. Tenant-scoped; idempotent per-document replace."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def replace_for_document(
        self, tenant_id: str, document_id: str, records: list[ExtractedRecord]
    ) -> None:
        with self._db.connection() as conn, conn.transaction():
            conn.execute(
                "DELETE FROM extracted_records WHERE tenant_id=%s AND document_id=%s",
                (tenant_id, document_id),
            )
            for r in records:
                conn.execute(
                    f"INSERT INTO extracted_records ({_REC_COLUMNS}) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        r.id,
                        r.tenant_id,
                        r.document_id,
                        r.record_type,
                        r.source_page,
                        r.raw_text,
                        r.occurred_on,
                        r.amount_minor,
                        r.currency,
                        r.direction,
                        r.merchant_raw,
                        r.merchant_normalized,
                        r.description,
                        r.account_label,
                        r.confidence,
                    ),
                )

    def list_for_document(self, tenant_id: str, document_id: str) -> list[ExtractedRecord]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_REC_COLUMNS} FROM extracted_records "
                "WHERE tenant_id=%s AND document_id=%s ORDER BY occurred_on NULLS LAST, id",
                (tenant_id, document_id),
            ).fetchall()
        return [_row_to_record(r) for r in rows]
