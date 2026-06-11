"""PostgreSQL repository adapters. All reads are scoped by tenant_id (ADR-0007)."""

from __future__ import annotations

from typing import Any

from doktok_contracts.media import ExtractedTerm
from doktok_contracts.schemas import (
    AuditEvent,
    Document,
    DocumentChunk,
    DocumentEntity,
    DocumentStatus,
    EntitySummary,
    EntityType,
    IngestionJob,
    JobStatus,
    StatsSummary,
)
from psycopg.rows import dict_row
from psycopg.types.json import Json

from doktok_storage_postgres.db import Database


def to_vector_literal(values: list[float]) -> str:
    """Format a float vector as a pgvector literal, e.g. ``[0.1,0.2,0.3]``."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


_DOC_COLUMNS = (
    "id, tenant_id, current_version_id, sha256, original_filename, detected_mime, "
    "title, status, storage_path, created_at, activated_at, metadata"
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
        metadata=row["metadata"] or {},
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


class PostgresDocumentRepository:
    """``DocumentRepository`` backed by PostgreSQL. Tenant-scoped reads."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, document: Document) -> None:
        with self._db.connection() as conn:
            conn.execute(
                f"INSERT INTO documents ({_DOC_COLUMNS}) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                    Json(document.metadata),
                ),
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
        return StatsSummary(
            documents=documents,
            jobs={row["status"]: int(row["n"]) for row in job_rows},
            entities=entities,
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
