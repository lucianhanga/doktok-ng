"""PostgreSQL repository adapters. All reads are scoped by tenant_id (ADR-0007)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from doktok_contracts.errors import DuplicateActiveDocumentError
from doktok_contracts.media import ExtractedTerm
from doktok_contracts.schemas import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    AggregationBucket,
    AggregationIntent,
    AggregationResult,
    AiSettings,
    AuditEvent,
    Category,
    CategorySummary,
    ChatMessage,
    ChatThread,
    Citation,
    ConfidenceBuckets,
    Document,
    DocumentChunk,
    DocumentEntity,
    DocumentFeature,
    DocumentRecordSummary,
    DocumentSort,
    DocumentStatus,
    EmbeddingProjection,
    EntitySummary,
    EntityType,
    ExtractedRecord,
    FeatureMetrics,
    FeatureStatus,
    IngestionJob,
    JobStatus,
    KgEdge,
    KgEdgeProvenance,
    KgEntity,
    KgEntityMention,
    ListAnchor,
    MerchantRollup,
    OcrSettings,
    ProjectionPoint,
    ProjectionRequest,
    RankedChunk,
    RecordCurrencyRollup,
    RecordTypeCount,
    SortDir,
    StatsSummary,
    TokenMatch,
    TokenSuggestion,
    TurnMetrics,
)
from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg.types.json import Json

from doktok_storage_postgres.crypto import decrypt_secret, encrypt_secret
from doktok_storage_postgres.db import Database


def to_vector_literal(values: list[float]) -> str:
    """Format a float vector as a pgvector literal, e.g. ``[0.1,0.2,0.3]``."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def from_vector_literal(raw: str | None) -> list[float]:
    """Parse a pgvector text value (``[0.1,0.2,...]``) back into a float list; ``[]`` for NULL."""
    if not raw:
        return []
    return [float(part) for part in raw.strip().lstrip("[").rstrip("]").split(",") if part]


_DOC_COLUMNS = (
    "id, tenant_id, current_version_id, sha256, original_filename, detected_mime, "
    "title, status, storage_path, created_at, activated_at, duplicate_of, metadata, "
    "ingested_at, document_date, location, summary, unidentifiable"
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
        unidentifiable=row["unidentifiable"],
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

    def delete_for_document(self, tenant_id: str, document_id: str) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "DELETE FROM ingestion_jobs WHERE tenant_id=%s AND document_id=%s",
                (tenant_id, document_id),
            )

    def list_in_flight(self, tenant_id: str, *, before: datetime) -> list[IngestionJob]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_COLUMNS} FROM ingestion_jobs WHERE tenant_id=%s "
                "AND status NOT IN ('active', 'failed', 'quarantined', 'duplicate') "
                "AND created_at < %s ORDER BY created_at",
                (tenant_id, before),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def delete(self, tenant_id: str, job_id: str) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "DELETE FROM ingestion_jobs WHERE tenant_id=%s AND id=%s",
                (tenant_id, job_id),
            )


# Sort key -> (SQL ORDER BY expression, is_nullable, cursor-value cast). Static allowlist: the
# request only ever supplies a DocumentSort enum, never raw SQL, so dynamic ORDER BY can't be
# injected. The cast pins the cursor parameter's type (Postgres can't infer it from `IS NOT NULL`).
# "acquired" uses created_at (set at ingest, never null) and rides the existing keyset index;
# "category" is the alphabetically-first active category name (the one ordering not index-only).
_SORT_EXPR: dict[DocumentSort, tuple[str, bool, str]] = {
    DocumentSort.ACQUIRED: ("d.created_at", False, "timestamptz"),
    DocumentSort.CREATED: ("d.document_date", True, "date"),
    DocumentSort.TITLE: ("lower(d.title)", True, "text"),
    DocumentSort.CATEGORY: (
        "(SELECT min(c.name) FROM document_category_links l "
        "JOIN categories c ON c.id = l.category_id AND c.tenant_id = l.tenant_id "
        "WHERE l.document_id = d.id AND l.tenant_id = d.tenant_id AND c.status = 'active')",
        True,
        "text",
    ),
}


def _like_contains(value: str) -> str:
    """A case-insensitive LIKE pattern matching ``value`` as a literal substring (wildcards in the
    user's input are escaped so '50%' or 'a_b' match literally; backslash is the ESCAPE char)."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _doc_filter_sql(
    tenant_id: str,
    *,
    status: DocumentStatus | None,
    category: str | None,
    needs_attention: bool,
    unidentifiable: bool | None,
    title: str | None,
    tokens: tuple[str, ...],
    token_type: EntityType | None,
    token_match: TokenMatch,
) -> tuple[str, dict[str, Any]]:
    """Build the shared document-filter WHERE clause (status + category + needs-attention +
    unidentifiable + title + tokens) and its parameters. Used by both ``list_documents`` (adds
    keyset + order) and ``list_document_ids``. All values are parameterized; tokens go in as a
    single ``text[]``."""
    distinct_tokens = tuple(dict.fromkeys(tokens))  # dedupe, keep order
    title_clean = title.strip() if title else None
    params: dict[str, Any] = {
        "tenant": tenant_id,
        "status": status.value if status else None,
        "category": category,
        "needs_attention": needs_attention,
        "unidentifiable": unidentifiable,
        "title": title_clean,
        "title_like": _like_contains(title_clean) if title_clean else None,
        "tokens": list(distinct_tokens),
        "token_type": token_type.value if token_type else None,
        "token_count": len(distinct_tokens),
    }
    where = (
        "d.tenant_id = %(tenant)s "
        "AND (%(status)s::text IS NULL OR d.status = %(status)s) "
        "AND (%(title)s::text IS NULL OR d.title ILIKE %(title_like)s) "
        "AND (%(category)s::text IS NULL OR EXISTS ("
        "  SELECT 1 FROM document_category_links l JOIN categories c ON c.id = l.category_id "
        "  WHERE l.document_id = d.id AND l.tenant_id = d.tenant_id "
        "  AND c.name = %(category)s AND c.status = 'active')) "
        # 'Needs attention' = a real problem: a FAILED feature (red badge). Pending/running features
        # are still processing (not a problem), and a not-yet-created feature is seeded pending then
        # finishes or fails - so 'failed' is the steady-state actionable signal.
        "AND (NOT %(needs_attention)s OR EXISTS ("
        "  SELECT 1 FROM document_features f "
        "  WHERE f.tenant_id = d.tenant_id AND f.document_id = d.id AND f.status = 'failed')) "
        # unidentifiable: True = only flagged; False = exclude flagged (NULL 'unassessed' stays).
        "AND (%(unidentifiable)s::boolean IS NULL "
        "  OR (%(unidentifiable)s::boolean IS TRUE AND d.unidentifiable IS TRUE) "
        "  OR (%(unidentifiable)s::boolean IS FALSE AND d.unidentifiable IS NOT TRUE))"
    )
    if distinct_tokens:
        # Tenant-scoped EXISTS over document_entities; ALL counts distinct matches == requested.
        entity_pred = (
            "SELECT 1 FROM document_entities e "
            "WHERE e.tenant_id = d.tenant_id AND e.document_id = d.id "
            "AND (%(token_type)s::text IS NULL OR e.entity_type = %(token_type)s) "
            "AND e.normalized_value = ANY(%(tokens)s)"
        )
        if token_match is TokenMatch.ALL:
            where += (
                " AND (SELECT count(DISTINCT e.normalized_value) FROM document_entities e "
                "WHERE e.tenant_id = d.tenant_id AND e.document_id = d.id "
                "AND (%(token_type)s::text IS NULL OR e.entity_type = %(token_type)s) "
                "AND e.normalized_value = ANY(%(tokens)s)) = %(token_count)s"
            )
        else:
            where += f" AND EXISTS ({entity_pred})"
    return where, params


class PostgresDocumentRepository:
    """``DocumentRepository`` backed by PostgreSQL. Tenant-scoped reads."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, document: Document) -> None:
        try:
            with self._db.connection() as conn:
                placeholders = ", ".join(["%s"] * len(_DOC_COLUMNS.split(", ")))
                conn.execute(
                    f"INSERT INTO documents ({_DOC_COLUMNS}) VALUES ({placeholders})",
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
                        document.unidentifiable,
                    ),
                )
        except pg_errors.UniqueViolation as exc:
            # The active content-dedup constraint fired (a concurrent ingest of the same content
            # won the race). Translate to a domain error so the pipeline marks this copy duplicate.
            if getattr(exc.diag, "constraint_name", "") == "uq_documents_active_sha":
                raise DuplicateActiveDocumentError(str(exc)) from exc
            raise

    def find_active_by_sha256(self, tenant_id: str, sha256: str) -> str | None:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            row = cur.execute(
                "SELECT id FROM documents WHERE tenant_id=%s AND sha256=%s AND status='active' "
                "LIMIT 1",
                (tenant_id, sha256),
            ).fetchone()
        return str(row["id"]) if row else None

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

    def set_unidentifiable(self, tenant_id: str, document_id: str, *, value: bool | None) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "UPDATE documents SET unidentifiable=%s WHERE id=%s AND tenant_id=%s",
                (value, document_id, tenant_id),
            )

    def activate(
        self,
        tenant_id: str,
        document_id: str,
        *,
        storage_path: str,
        metadata: dict[str, object],
    ) -> bool:
        # Setting status='active' enforces uq_documents_active_sha (partial unique on active rows):
        # a content race surfaces as UniqueViolation, translated to DuplicateActiveDocumentError so
        # the caller records a duplicate (mirrors `add`).
        try:
            with self._db.connection() as conn:
                cur = conn.execute(
                    "UPDATE documents SET status='active', storage_path=%s, metadata=%s, "
                    "activated_at=now(), ingested_at=now() "
                    "WHERE id=%s AND tenant_id=%s AND status='processing'",
                    (storage_path, Json(metadata), document_id, tenant_id),
                )
                return cur.rowcount > 0
        except pg_errors.UniqueViolation as exc:
            raise DuplicateActiveDocumentError(str(exc)) from exc

    def get(self, tenant_id: str, document_id: str) -> Document | None:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            row = cur.execute(
                f"SELECT {_DOC_COLUMNS} FROM documents WHERE id=%s AND tenant_id=%s",
                (document_id, tenant_id),
            ).fetchone()
        return _row_to_document(row) if row else None

    def list_documents(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        cursor: ListAnchor | None = None,
        status: DocumentStatus | None = None,
        category: str | None = None,
        needs_attention: bool = False,
        unidentifiable: bool | None = None,
        sort: DocumentSort = DocumentSort.ACQUIRED,
        direction: SortDir = SortDir.DESC,
        title: str | None = None,
        tokens: tuple[str, ...] = (),
        token_type: EntityType | None = None,
        token_match: TokenMatch = TokenMatch.ALL,
    ) -> tuple[list[Document], int, ListAnchor | None]:
        where, params = _doc_filter_sql(
            tenant_id,
            status=status,
            category=category,
            needs_attention=needs_attention,
            unidentifiable=unidentifiable,
            title=title,
            tokens=tokens,
            token_type=token_type,
            token_match=token_match,
        )
        expr, nullable, cast = _SORT_EXPR[sort]
        cv = f"%(cur_val)s::{cast}"  # typed cursor value (Postgres can't infer it from IS NOT NULL)
        # Tie-break by id in the SAME direction as the sort key so the (value, id) ordering is a
        # total order a single index can satisfy; nulls always sort last (explicit, not PG default).
        op = "<" if direction is SortDir.DESC else ">"
        dir_sql = "DESC" if direction is SortDir.DESC else "ASC"
        order_by = f"ORDER BY {expr} {dir_sql} NULLS LAST, d.id {dir_sql}"
        params["has_cursor"] = cursor is not None
        params["cur_val"] = cursor.value if cursor else None
        params["cur_id"] = cursor.doc_id if cursor else None
        if nullable:
            # Three branches because NULLS LAST breaks a plain row comparison: past-in-value, equal
            # value past-in-id, or the trailing null block.
            keyset = (
                f" AND (NOT %(has_cursor)s"
                f" OR ({cv} IS NOT NULL AND ({expr} {op} {cv}"
                f" OR ({expr} = {cv} AND d.id {op} %(cur_id)s) OR {expr} IS NULL))"
                f" OR ({cv} IS NULL AND {expr} IS NULL AND d.id {op} %(cur_id)s))"
            )
        else:
            keyset = f" AND (NOT %(has_cursor)s OR ({expr}, d.id) {op} ({cv}, %(cur_id)s))"
        params["limit_plus_1"] = limit + 1
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_DOC_COLUMNS_D}, {expr} AS sort_value FROM documents d "
                f"WHERE {where}{keyset} {order_by} LIMIT %(limit_plus_1)s",
                params,
            ).fetchall()
            total_row = cur.execute(
                f"SELECT COUNT(*) AS n FROM documents d WHERE {where}", params
            ).fetchone()
            total = total_row["n"] if total_row else 0
        has_more = len(rows) > limit
        rows = rows[:limit]
        documents = [_row_to_document(row) for row in rows]
        next_anchor = (
            ListAnchor(
                sort=sort, direction=direction, value=rows[-1]["sort_value"], doc_id=rows[-1]["id"]
            )
            if has_more and rows
            else None
        )
        return documents, int(total), next_anchor

    def list_document_ids(
        self,
        tenant_id: str,
        *,
        status: DocumentStatus | None = None,
        category: str | None = None,
        needs_attention: bool = False,
        unidentifiable: bool | None = None,
        title: str | None = None,
        tokens: tuple[str, ...] = (),
        token_type: EntityType | None = None,
        token_match: TokenMatch = TokenMatch.ALL,
        cap: int = 10_000,
    ) -> tuple[list[str], int, bool]:
        where, params = _doc_filter_sql(
            tenant_id,
            status=status,
            category=category,
            needs_attention=needs_attention,
            unidentifiable=unidentifiable,
            title=title,
            tokens=tokens,
            token_type=token_type,
            token_match=token_match,
        )
        params["cap"] = cap
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT d.id FROM documents d WHERE {where} ORDER BY d.id LIMIT %(cap)s",
                params,
            ).fetchall()
            total_row = cur.execute(
                f"SELECT COUNT(*) AS n FROM documents d WHERE {where}", params
            ).fetchone()
            total = int(total_row["n"]) if total_row else 0
        return [r["id"] for r in rows], total, total > cap

    def delete(self, tenant_id: str, document_id: str) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "DELETE FROM documents WHERE id=%s AND tenant_id=%s",
                (document_id, tenant_id),
            )


_ACTIVITY_COLUMNS = (
    "id, tenant_id, document_id, job_id, doc_filename, doc_title, phase, event_type, severity, "
    "record_kind, record_id, actor, actor_kind, description, occurred_at, detail"
)


def _row_to_event(row: dict[str, Any]) -> AuditEvent:
    return AuditEvent(
        id=row["id"],
        tenant_id=row["tenant_id"],
        event_type=row["event_type"],
        actor=row["actor"],
        document_id=row["document_id"],
        job_id=row["job_id"],
        timestamp=row["occurred_at"],
        metadata=row["detail"] or {},
        severity=row["severity"],
        phase=row["phase"] or "",
        description=row["description"] or "",
        actor_kind=row["actor_kind"],
        record_kind=row["record_kind"],
        record_id=row["record_id"],
        doc_filename=row["doc_filename"],
        doc_title=row["doc_title"],
    )


class PostgresAuditLogRepository:
    """``AuditLogRepository`` backed by PostgreSQL ``document_activity``. Append-only: record +
    tenant-scoped reads. ``doc_filename``/``doc_title`` are snapshotted at write time (from the
    ``documents`` row when not supplied) so a row stays readable after the document is deleted."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, event: AuditEvent) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "INSERT INTO document_activity "
                "(id, tenant_id, document_id, job_id, doc_filename, doc_title, phase, event_type, "
                "severity, record_kind, record_id, actor, actor_kind, description, occurred_at, "
                "detail) VALUES (%s, %s, %s, %s, "
                "COALESCE(%s, (SELECT original_filename FROM documents "
                "WHERE id=%s AND tenant_id=%s)), "
                "COALESCE(%s, (SELECT title FROM documents WHERE id=%s AND tenant_id=%s)), "
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (
                    event.id,
                    event.tenant_id,
                    event.document_id,
                    event.job_id,
                    event.doc_filename,
                    event.document_id,
                    event.tenant_id,
                    event.doc_title,
                    event.document_id,
                    event.tenant_id,
                    event.phase,
                    event.event_type,
                    event.severity,
                    event.record_kind,
                    event.record_id,
                    event.actor,
                    event.actor_kind,
                    event.description,
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
                f"SELECT {_ACTIVITY_COLUMNS} FROM document_activity {clause} "
                "ORDER BY occurred_at DESC, id DESC LIMIT %s OFFSET %s",
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
        # One transaction: a crash mid-loop must not leave a document with a partial chunk set.
        with self._db.connection() as conn, conn.transaction():
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

    def read_embeddings(self, tenant_id: str, limit: int) -> list[tuple[str, str, list[float]]]:
        with self._db.connection() as conn:
            rows = conn.execute(
                "SELECT id, document_id, embedding::text FROM document_chunks "
                "WHERE tenant_id=%s AND embedding IS NOT NULL ORDER BY id LIMIT %s",
                (tenant_id, limit),
            ).fetchall()
        return [(r[0], r[1], from_vector_literal(r[2])) for r in rows]

    def embedding_fingerprint(self, tenant_id: str) -> str:
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT count(*), coalesce(max(created_at)::text, '') FROM document_chunks "
                "WHERE tenant_id=%s AND embedding IS NOT NULL",
                (tenant_id,),
            ).fetchone()
        count, latest = (row[0], row[1]) if row else (0, "")
        return f"chunks={count};latest={latest}"

    def read_texts(self, tenant_id: str, chunk_ids: list[str]) -> dict[str, str]:
        if not chunk_ids:
            return {}
        with self._db.connection() as conn:
            rows = conn.execute(
                "SELECT id, text FROM document_chunks WHERE tenant_id=%s AND id = ANY(%s)",
                (tenant_id, list(chunk_ids)),
            ).fetchall()
        return {r[0]: r[1] for r in rows}


class PostgresEntityRepository:
    """``EntityRepository`` backed by PostgreSQL. Tenant-scoped."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add_entities(self, entities: list[DocumentEntity]) -> None:
        if not entities:
            return
        # One transaction: a crash mid-loop must not leave a document with a partial entity set.
        with self._db.connection() as conn, conn.transaction():
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

    def delete_for_document_types(
        self, tenant_id: str, document_id: str, entity_types: list[str]
    ) -> None:
        if not entity_types:
            return
        with self._db.connection() as conn:
            conn.execute(
                "DELETE FROM document_entities WHERE tenant_id=%s AND document_id=%s "
                "AND entity_type = ANY(%s)",
                (tenant_id, document_id, entity_types),
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


class PostgresKnowledgeGraphRepository:
    """``KnowledgeGraphRepository`` on PostgreSQL (KAG Phase 1). Tenant-scoped, idempotent."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert_entities(self, entities: list[KgEntity]) -> None:
        if not entities:
            return
        # DO NOTHING: the node identity is a deterministic function of type+value, so an existing
        # node is already correct - re-running must not mutate it (keeps the feature idempotent).
        with self._db.connection() as conn, conn.transaction():
            for entity in entities:
                conn.execute(
                    "INSERT INTO kg_entities "
                    "(id, tenant_id, entity_type, normalized_value, metadata) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                    (
                        entity.id,
                        entity.tenant_id,
                        entity.entity_type.value,
                        entity.normalized_value,
                        Json(entity.metadata),
                    ),
                )

    def replace_mentions_for_document(
        self, tenant_id: str, document_id: str, mentions: list[KgEntityMention]
    ) -> None:
        # One transaction: delete the document's prior links then insert the current set, so a crash
        # never leaves a half-resolved document.
        with self._db.connection() as conn, conn.transaction():
            conn.execute(
                "DELETE FROM kg_entity_mentions WHERE tenant_id=%s AND document_id=%s",
                (tenant_id, document_id),
            )
            for mention in mentions:
                conn.execute(
                    "INSERT INTO kg_entity_mentions "
                    "(mention_id, tenant_id, canonical_entity_id, document_id, chunk_id, "
                    "entity_type, normalized_value) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        mention.mention_id,
                        mention.tenant_id,
                        mention.canonical_entity_id,
                        mention.document_id,
                        mention.chunk_id,
                        mention.entity_type.value,
                        mention.normalized_value,
                    ),
                )

    def get_entity(self, tenant_id: str, entity_id: str) -> KgEntity | None:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            row = cur.execute(
                "SELECT id, tenant_id, entity_type, normalized_value, metadata "
                "FROM kg_entities WHERE tenant_id=%s AND id=%s",
                (tenant_id, entity_id),
            ).fetchone()
        return _row_to_kg_entity(row) if row else None

    def mentions_for_document(self, tenant_id: str, document_id: str) -> list[KgEntityMention]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_KG_MENTION_COLUMNS} FROM kg_entity_mentions "
                "WHERE tenant_id=%s AND document_id=%s ORDER BY mention_id",
                (tenant_id, document_id),
            ).fetchall()
        return [_row_to_kg_mention(row) for row in rows]

    def mentions_for_entity(self, tenant_id: str, entity_id: str) -> list[KgEntityMention]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_KG_MENTION_COLUMNS} FROM kg_entity_mentions "
                "WHERE tenant_id=%s AND canonical_entity_id=%s ORDER BY mention_id",
                (tenant_id, entity_id),
            ).fetchall()
        return [_row_to_kg_mention(row) for row in rows]

    def entity_count(self, tenant_id: str) -> int:
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT count(*) FROM kg_entities WHERE tenant_id=%s",
                (tenant_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ Phase 2: edges

    def replace_edges_for_document(
        self,
        tenant_id: str,
        document_id: str,
        edges: list[KgEdge],
        provenance: list[KgEdgeProvenance],
    ) -> None:
        """Idempotently replace all edges contributed by this document."""
        with self._db.connection() as conn, conn.transaction():
            # Edges that currently draw provenance from this document: they may lose evidence below
            # (a removed/changed document), so they MUST be recomputed + considered for pruning even
            # when they are not among the new edges/provenance - otherwise a now-orphaned edge keeps
            # its stale evidence_count and is never pruned.
            prior = conn.execute(
                "SELECT DISTINCT edge_id FROM kg_edge_provenance "
                "WHERE tenant_id=%s AND document_id=%s",
                (tenant_id, document_id),
            ).fetchall()
            prior_edge_ids = {row[0] for row in prior}
            # Step 1: remove old provenance for this document
            conn.execute(
                "DELETE FROM kg_edge_provenance WHERE tenant_id=%s AND document_id=%s",
                (tenant_id, document_id),
            )
            # Step 2: upsert edge rows (idempotent; DO UPDATE to refresh updated_at)
            for edge in edges:
                conn.execute(
                    "INSERT INTO kg_edges "
                    "(id, tenant_id, src_entity_id, predicate, "
                    "dst_entity_id, evidence_count, metadata) "
                    "VALUES (%s,%s,%s,%s,%s,0,%s) "
                    "ON CONFLICT (id) DO UPDATE SET updated_at=now()",
                    (
                        edge.id,
                        edge.tenant_id,
                        edge.src_entity_id,
                        edge.predicate,
                        edge.dst_entity_id,
                        Json(edge.metadata),
                    ),
                )
            # Step 3: insert new provenance rows
            for prov in provenance:
                conn.execute(
                    "INSERT INTO kg_edge_provenance "
                    "(id, tenant_id, edge_id, document_id, chunk_id, evidence) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        prov.id,
                        prov.tenant_id,
                        prov.edge_id,
                        prov.document_id,
                        prov.chunk_id,
                        prov.evidence,
                    ),
                )
            # Step 4: recompute evidence_count for all affected edges (new ones AND any that lost
            # this document's provenance, so an emptied edge drops to 0 and is pruned in step 5)
            edge_ids = list(
                prior_edge_ids | {e.id for e in edges} | {p.edge_id for p in provenance}
            )
            if edge_ids:
                conn.execute(
                    "UPDATE kg_edges SET evidence_count = ("
                    "  SELECT count(*) FROM kg_edge_provenance WHERE edge_id=kg_edges.id"
                    ") WHERE id = ANY(%s)",
                    (edge_ids,),
                )
            # Step 5: prune edges with zero evidence_count (no more provenance from any document)
            conn.execute(
                "DELETE FROM kg_edges WHERE tenant_id=%s AND evidence_count=0",
                (tenant_id,),
            )

    def edges_for_entity(self, tenant_id: str, entity_id: str) -> list[KgEdge]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                "SELECT id, tenant_id, src_entity_id, predicate, dst_entity_id, "
                "evidence_count, metadata "
                "FROM kg_edges "
                "WHERE tenant_id=%s AND (src_entity_id=%s OR dst_entity_id=%s) "
                "ORDER BY id",
                (tenant_id, entity_id, entity_id),
            ).fetchall()
        return [_row_to_kg_edge(row) for row in rows]

    def edge_count(self, tenant_id: str) -> int:
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT count(*) FROM kg_edges WHERE tenant_id=%s",
                (tenant_id,),
            ).fetchone()
        return int(row[0]) if row else 0


_KG_MENTION_COLUMNS = (
    "mention_id, tenant_id, canonical_entity_id, document_id, chunk_id, "
    "entity_type, normalized_value"
)


def _row_to_kg_entity(row: dict[str, Any]) -> KgEntity:
    return KgEntity(
        id=row["id"],
        tenant_id=row["tenant_id"],
        entity_type=EntityType(row["entity_type"]),
        normalized_value=row["normalized_value"],
        metadata=row["metadata"] or {},
    )


def _row_to_kg_mention(row: dict[str, Any]) -> KgEntityMention:
    return KgEntityMention(
        mention_id=row["mention_id"],
        tenant_id=row["tenant_id"],
        canonical_entity_id=row["canonical_entity_id"],
        document_id=row["document_id"],
        chunk_id=row["chunk_id"],
        entity_type=EntityType(row["entity_type"]),
        normalized_value=row["normalized_value"],
    )


def _row_to_kg_edge(row: dict[str, Any]) -> KgEdge:
    return KgEdge(
        id=row["id"],
        tenant_id=row["tenant_id"],
        src_entity_id=row["src_entity_id"],
        predicate=row["predicate"],
        dst_entity_id=row["dst_entity_id"],
        evidence_count=row["evidence_count"],
        metadata=row["metadata"] or {},
    )


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
            # Documents needing attention = those with a FAILED feature (not merely in-progress).
            pending_feature_docs = self._scalar(
                cur,
                "SELECT COUNT(DISTINCT document_id) AS n FROM document_features "
                "WHERE tenant_id=%s AND status = 'failed'",
                tenant_id,
            )
            # Documents with work in flight = a feature queued or running (e.g. a re-extraction the
            # reconciler hasn't finished). These never show as a non-terminal ingestion job, so the
            # overview's "Processing" count was blind to them.
            processing_feature_docs = self._scalar(
                cur,
                "SELECT COUNT(DISTINCT document_id) AS n FROM document_features "
                "WHERE tenant_id=%s AND status IN ('pending', 'running')",
                tenant_id,
            )
        return StatsSummary(
            documents=documents,
            jobs={row["status"]: int(row["n"]) for row in job_rows},
            entities=entities,
            documents_pending_features=pending_feature_docs,
            documents_processing_features=processing_feature_docs,
        )


# Encoding/markup leftovers that survive tokenization as bare words even after HTML tags are
# stripped (e.g. raw "http://..." URLs, the "aHR" base64 prefix of "http", HTML entities). None are
# meaningful keywords, so they are dropped outright. HTML *tag* names (td/tr/table/...) are removed
# upstream by stripping whole "<...>" spans, which also keeps real prose words like "table".
_MARKUP_NOISE_TERMS: frozenset[str] = frozenset(
    {"http", "https", "www", "ahr", "href", "nbsp", "amp", "quot", "apos", "rsquo", "lsquo"}
)


class PostgresLexicalTermExtractor:
    """``LexicalTermExtractor`` using PostgreSQL full-text lexemes (stopwords removed, stemmed).

    ``to_tsvector(config, text)`` normalizes the document into significant lexemes for the given
    language config; ``unnest`` exposes each lexeme with its positions so we can rank by frequency.

    Noise is filtered out: HTML markup (``<td>``/``<tr>``/``<img src=data:...>`` emitted by some OCR
    engines) is stripped before tokenizing so tag names and embedded blobs never become terms;
    lexemes shorter than 3 chars, those containing digits (codes, postal codes, base64 fragments),
    and a small set of encoding leftovers are dropped.
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
                "FROM unnest(to_tsvector(%s::regconfig, regexp_replace(%s, '<[^>]+>', ' ', 'g'))) "
                "WHERE length(lexeme) >= 3 AND lexeme !~ '[0-9]' AND lexeme <> ALL(%s) "
                "ORDER BY freq DESC, lexeme ASC LIMIT %s",
                (config, text, list(_MARKUP_NOISE_TERMS), limit),
            ).fetchall()
        return [ExtractedTerm(term=row["lexeme"], frequency=int(row["freq"])) for row in rows]


_FEATURE_COLUMNS = (
    "id, tenant_id, document_id, feature, feature_version, status, attempts, max_attempts, "
    "last_error, last_attempt_at, completed_at, next_attempt_at, created_at, updated_at, metrics"
)
_FEATURE_COLUMNS_F = ", ".join(f"f.{c}" for c in _FEATURE_COLUMNS.split(", "))


def _row_to_feature(row: dict[str, Any]) -> DocumentFeature:
    # ``metrics`` is jsonb (psycopg returns a dict); old rows / the column default are '{}', which
    # FeatureMetrics validates to all-zeros (backward compatible).
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
        metrics=FeatureMetrics.model_validate(row.get("metrics") or {}),
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
        # One transaction so the backfill INSERT + version-bump UPDATE for all features apply
        # together (a crash can't leave the ledger half-reconciled).
        with self._db.connection() as conn, conn.transaction():
            for name, version in features:
                cur = conn.execute(
                    "INSERT INTO document_features "
                    "(id, tenant_id, document_id, feature, feature_version, status) "
                    "SELECT gen_random_uuid()::text, d.tenant_id, d.id, %s, %s, 'pending' "
                    "FROM documents d WHERE d.tenant_id=%s AND d.status='active' "
                    "AND NOT EXISTS (SELECT 1 FROM document_features f "
                    "WHERE f.tenant_id=d.tenant_id AND f.document_id=d.id AND f.feature=%s) "
                    # NOT EXISTS narrows the work, but the ingestion pipeline can insert the same
                    # (doc, feature) inline between the check and this INSERT (separate thread), so
                    # guard the race instead of aborting the whole reconcile pass.
                    "ON CONFLICT (tenant_id, document_id, feature) DO NOTHING",
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
            # 'extract' is an inline activation marker (the 'text' badge), not a reconciler
            # processor, so the loop above never seeds it - a document activated by a path that
            # skipped the inline write would silently lack the badge forever. An active document IS
            # extracted by definition, so record extract done for any active doc missing it; the
            # missing badge self-heals on the next reconcile pass. (In staged mode the registered
            # ExtractStage already seeds it, so NOT EXISTS makes this a no-op.)
            cur = conn.execute(
                "INSERT INTO document_features "
                "(id, tenant_id, document_id, feature, feature_version, status, completed_at) "
                "SELECT gen_random_uuid()::text, d.tenant_id, d.id, 'extract', 1, 'done', now() "
                "FROM documents d WHERE d.tenant_id=%s AND d.status='active' "
                "AND NOT EXISTS (SELECT 1 FROM document_features f "
                "WHERE f.tenant_id=d.tenant_id AND f.document_id=d.id AND f.feature='extract') "
                # Guard the same intake-vs-reconciler insert race as above (the pipeline writes the
                # inline 'extract' marker at activation on another thread).
                "ON CONFLICT (tenant_id, document_id, feature) DO NOTHING",
                (tenant_id,),
            )
            affected += cur.rowcount
        return affected

    def seed_for_document(
        self, tenant_id: str, document_id: str, stages: list[tuple[str, int]]
    ) -> int:
        affected = 0
        with self._db.connection() as conn, conn.transaction():
            for name, version in stages:
                cur = conn.execute(
                    "INSERT INTO document_features "
                    "(id, tenant_id, document_id, feature, feature_version, status) "
                    "VALUES (gen_random_uuid()::text, %s, %s, %s, %s, 'pending') "
                    "ON CONFLICT (tenant_id, document_id, feature) DO NOTHING",
                    (tenant_id, document_id, name, version),
                )
                affected += cur.rowcount
        return affected

    def claim_next(
        self,
        tenant_id: str,
        *,
        now: datetime,
        reclaim_before: datetime,
        dependencies: Sequence[tuple[str, str]] = (),
    ) -> DocumentFeature | None:
        params = {
            "tenant": tenant_id,
            "now": now,
            "reclaim_before": reclaim_before,
            "dep_features": [d[0] for d in dependencies],
            "dep_prereqs": [d[1] for d in dependencies],
        }
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            row = cur.execute(
                # ``deps`` is the (feature, prerequisite) edge set. A row is only claimable when
                # none of its prerequisites is still missing a 'done' row on the same document - so
                # a stage waits for its inputs. Empty deps -> no gating (backward compatible).
                "WITH deps(feature, prereq) AS ("
                "  SELECT * FROM unnest(%(dep_features)s::text[], %(dep_prereqs)s::text[])"
                "    AS t(feature, prereq)"
                "), due AS ("
                "  SELECT f.id FROM document_features f WHERE f.tenant_id=%(tenant)s AND ("
                "    f.status='pending'"
                "    OR (f.status='failed' AND f.attempts < f.max_attempts "
                "        AND (f.next_attempt_at IS NULL OR f.next_attempt_at <= %(now)s))"
                "    OR (f.status='running' AND f.last_attempt_at < %(reclaim_before)s))"
                "  AND NOT EXISTS ("
                "    SELECT 1 FROM deps d WHERE d.feature = f.feature AND NOT EXISTS ("
                "      SELECT 1 FROM document_features p WHERE p.tenant_id = f.tenant_id"
                "        AND p.document_id = f.document_id AND p.feature = d.prereq"
                "        AND p.status = 'done'))"
                "  ORDER BY f.created_at FOR UPDATE SKIP LOCKED LIMIT 1) "
                "UPDATE document_features f SET status='running', attempts=f.attempts+1, "
                "last_attempt_at=%(now)s, updated_at=now() FROM due WHERE f.id=due.id "
                f"RETURNING {_FEATURE_COLUMNS_F}",
                params,
            ).fetchone()
        return _row_to_feature(row) if row else None

    def mark_done(
        self, feature_id: str, *, feature_version: int, metrics: FeatureMetrics | None = None
    ) -> None:
        # When metrics are supplied, persist them onto the row (jsonb); otherwise leave the column
        # as-is so a re-run without measurement doesn't clobber a prior measurement.
        with self._db.connection() as conn:
            if metrics is None:
                conn.execute(
                    "UPDATE document_features SET status='done', feature_version=%s, "
                    "completed_at=now(), last_error=NULL, updated_at=now() WHERE id=%s",
                    (feature_version, feature_id),
                )
            else:
                conn.execute(
                    "UPDATE document_features SET status='done', feature_version=%s, "
                    "completed_at=now(), last_error=NULL, metrics=%s, updated_at=now() WHERE id=%s",
                    (feature_version, Json(metrics.model_dump()), feature_id),
                )

    def feature_counts_for_documents(
        self, tenant_id: str, document_ids: list[str]
    ) -> dict[str, tuple[int, int]]:
        """(done, failed) feature counts per document for the list tooltip, in ONE batched GROUP BY
        over the page's ids (never per-row). Documents with no rows are absent from the map."""
        if not document_ids:
            return {}
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                "SELECT document_id, "
                "COUNT(*) FILTER (WHERE status='done') AS done, "
                "COUNT(*) FILTER (WHERE status='failed') AS failed "
                "FROM document_features WHERE tenant_id=%s AND document_id = ANY(%s) "
                "GROUP BY document_id",
                (tenant_id, list(document_ids)),
            ).fetchall()
        return {r["document_id"]: (int(r["done"]), int(r["failed"])) for r in rows}

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

    def list_for_documents(self, tenant_id: str, document_ids: list[str]) -> list[DocumentFeature]:
        if not document_ids:
            return []
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_FEATURE_COLUMNS} FROM document_features "
                "WHERE tenant_id=%s AND document_id = ANY(%s) ORDER BY document_id, feature",
                (tenant_id, list(document_ids)),
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

    def requeue_running(self, tenant_id: str) -> int:
        # Keep attempts as-is (the orphaned attempt counted); clearing last_attempt_at + status
        # makes claim_next pick it up on the next pass instead of after the lease window.
        with self._db.connection() as conn:
            cur = conn.execute(
                "UPDATE document_features SET status='pending', last_attempt_at=NULL, "
                "updated_at=now() WHERE tenant_id=%s AND status='running'",
                (tenant_id,),
            )
            return cur.rowcount


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

    def primary_categories(self, tenant_id: str, document_ids: list[str]) -> dict[str, str]:
        if not document_ids:
            return {}
        # Rank each doc's categories by tenant-wide document count (name tiebreak); keep the top.
        with self._db.connection() as conn:
            rows = conn.execute(
                "WITH counts AS ("
                "  SELECT category_id, count(*) AS dc FROM document_category_links "
                "  WHERE tenant_id=%s GROUP BY category_id"
                "), ranked AS ("
                "  SELECT l.document_id, c.name, row_number() OVER ("
                "    PARTITION BY l.document_id ORDER BY co.dc DESC, c.name ASC) AS rn "
                "  FROM document_category_links l "
                "  JOIN categories c ON c.id = l.category_id AND c.status='active' "
                "  JOIN counts co ON co.category_id = l.category_id "
                "  WHERE l.tenant_id=%s AND l.document_id = ANY(%s)"
                ") SELECT document_id, name FROM ranked WHERE rn = 1",
                (tenant_id, tenant_id, list(document_ids)),
            ).fetchall()
        return {r[0]: r[1] for r in rows}


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

    def list_for_document_page(
        self, tenant_id: str, document_id: str, *, limit: int, offset: int
    ) -> tuple[list[ExtractedRecord], int]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            total = cur.execute(
                "SELECT COUNT(*) AS n FROM extracted_records WHERE tenant_id=%s AND document_id=%s",
                (tenant_id, document_id),
            ).fetchone()
            rows = cur.execute(
                f"SELECT {_REC_COLUMNS} FROM extracted_records "
                "WHERE tenant_id=%s AND document_id=%s "
                "ORDER BY occurred_on ASC NULLS LAST, id LIMIT %s OFFSET %s",
                (tenant_id, document_id, limit, offset),
            ).fetchall()
        return [_row_to_record(r) for r in rows], (total["n"] if total else 0)

    def record_summary(self, tenant_id: str, document_id: str) -> DocumentRecordSummary:
        # All queries are scoped to one document and served by idx_records_tenant_doc; the per-doc
        # slice is small, so these GROUP BYs are cheap enough to run eagerly on the detail card.
        scope = "WHERE tenant_id=%s AND document_id=%s"
        args = (tenant_id, document_id)
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            # Totals, date range, and confidence buckets in one pass. NULL confidence = unscored.
            agg = cur.execute(
                "SELECT COUNT(*) AS total, MIN(occurred_on) AS dmin, MAX(occurred_on) AS dmax, "
                "COUNT(*) FILTER (WHERE confidence >= %s) AS c_high, "
                "COUNT(*) FILTER (WHERE confidence >= %s AND confidence < %s) AS c_med, "
                "COUNT(*) FILTER (WHERE confidence < %s) AS c_low, "
                "COUNT(*) FILTER (WHERE confidence IS NULL) AS c_unscored "
                f"FROM extracted_records {scope}",
                (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, *args),
            ).fetchone()
            cur_rows = cur.execute(
                "SELECT currency, "
                "COALESCE(SUM(amount_minor) FILTER (WHERE direction='debit'), 0) AS debit_minor, "
                "COALESCE(SUM(amount_minor) FILTER (WHERE direction='credit'), 0) AS credit_minor, "
                f"COUNT(*) AS cnt FROM extracted_records {scope} "
                "GROUP BY currency ORDER BY cnt DESC, currency",
                args,
            ).fetchall()
            type_rows = cur.execute(
                f"SELECT record_type, COUNT(*) AS cnt FROM extracted_records {scope} "
                "GROUP BY record_type ORDER BY cnt DESC, record_type",
                args,
            ).fetchall()
            merch_rows = cur.execute(
                "SELECT merchant_normalized, currency, COUNT(*) AS cnt, "
                f"COALESCE(SUM(amount_minor), 0) AS total_minor FROM extracted_records {scope} "
                "AND merchant_normalized IS NOT NULL "
                "GROUP BY merchant_normalized, currency "
                "ORDER BY cnt DESC, merchant_normalized LIMIT 5",
                args,
            ).fetchall()

        if not agg or agg["total"] == 0:
            return DocumentRecordSummary()
        buckets = ConfidenceBuckets(
            high=agg["c_high"], medium=agg["c_med"], low=agg["c_low"], unscored=agg["c_unscored"]
        )
        return DocumentRecordSummary(
            total=agg["total"],
            by_currency=[
                RecordCurrencyRollup(
                    currency=(r["currency"].strip() if r["currency"] else None),
                    debit_minor=int(r["debit_minor"]),
                    credit_minor=int(r["credit_minor"]),
                    count=r["cnt"],
                )
                for r in cur_rows
            ],
            by_type=[
                RecordTypeCount(record_type=r["record_type"], count=r["cnt"]) for r in type_rows
            ],
            date_from=agg["dmin"],
            date_to=agg["dmax"],
            top_merchants=[
                MerchantRollup(
                    merchant=r["merchant_normalized"],
                    currency=(r["currency"].strip() if r["currency"] else None),
                    count=r["cnt"],
                    total_minor=int(r["total_minor"]),
                )
                for r in merch_rows
            ],
            confidence=buckets,
            low_confidence_count=buckets.low,
        )

    def aggregate(self, tenant_id: str, intent: AggregationIntent) -> AggregationResult:
        # Build a parameterized WHERE from the typed intent (never string-interpolated user input).
        where = ["tenant_id = %s"]
        params: list[Any] = [tenant_id]
        if intent.record_type:
            where.append("record_type = %s")
            params.append(intent.record_type)
        if intent.direction:
            where.append("direction = %s")
            params.append(intent.direction)
        if intent.currency:
            where.append("currency = %s")
            params.append(intent.currency)
        if intent.date_from:
            where.append("occurred_on >= %s")
            params.append(intent.date_from)
        if intent.date_to:
            where.append("occurred_on <= %s")
            params.append(intent.date_to)
        if intent.merchant:
            # Space-insensitive substring on the normalized merchant, so "block house" matches
            # "BLOCKHOUSE #42 HAMBURG" as well as "block house restaurant".
            where.append("replace(merchant_normalized, ' ', '') ILIKE %s")
            params.append(f"%{intent.merchant.strip().lower().replace(' ', '')}%")
        clause = " AND ".join(where)

        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                "SELECT currency, COALESCE(SUM(amount_minor), 0) AS total_minor, "
                f"COUNT(*) AS cnt FROM extracted_records WHERE {clause} "
                "GROUP BY currency ORDER BY cnt DESC",
                tuple(params),
            ).fetchall()
            samples: list[ExtractedRecord] = []
            if intent.sample_limit > 0:
                srows = cur.execute(
                    f"SELECT {_REC_COLUMNS} FROM extracted_records WHERE {clause} "
                    "ORDER BY occurred_on DESC NULLS LAST, id LIMIT %s",
                    (*params, intent.sample_limit),
                ).fetchall()
                samples = [_row_to_record(r) for r in srows]

        buckets = [
            AggregationBucket(
                currency=r["currency"], total_minor=int(r["total_minor"]), count=r["cnt"]
            )
            for r in rows
        ]
        return AggregationResult(
            operation=intent.operation,
            count=sum(b.count for b in buckets),
            by_currency=buckets,
            samples=samples,
        )


class PostgresAppSettingsRepository:
    """Global app settings backed by the ``app_settings`` key -> JSON table (not tenant-scoped)."""

    def __init__(self, db: Database, *, secrets_key: str = "", backup_status_dir: str = "") -> None:
        self._db = db
        self._secrets_key = secrets_key  # master key for at-rest secret encryption (APP-8)
        self._backup_status_dir = backup_status_dir  # per-leg backup sentinels dir (DRP, #368)

    def _get(self, key: str) -> Any:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            row = cur.execute("SELECT value FROM app_settings WHERE key=%s", (key,)).fetchone()
        return row["value"] if row else None

    def _set(self, key: str, value: Any) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
                (key, Json(value)),
            )

    def get_ai_settings(self) -> AiSettings:
        raw = self._get("ai_settings")
        return AiSettings.model_validate(raw) if raw else AiSettings()

    def set_ai_settings(self, settings: AiSettings) -> None:
        self._set("ai_settings", settings.model_dump())

    def has_ai_settings(self) -> bool:
        return self._get("ai_settings") is not None

    def get_no_egress(self) -> bool | None:
        raw = self._get("no_egress")
        return bool(raw) if raw is not None else None

    def set_no_egress(self, value: bool) -> None:
        self._set("no_egress", value)

    def get_openai_api_key(self) -> str:
        raw = self._get("openai_api_key")
        return decrypt_secret(str(raw), self._secrets_key) if raw else ""

    def set_openai_api_key(self, key: str) -> None:
        # Encrypt at rest when a master key is configured (APP-8); otherwise stored plaintext.
        self._set("openai_api_key", encrypt_secret(key, self._secrets_key))

    def get_ocr_settings(self) -> OcrSettings:
        raw = self._get("ocr_settings")
        return OcrSettings.model_validate(raw) if raw else OcrSettings()

    def set_ocr_settings(self, settings: OcrSettings) -> None:
        self._set("ocr_settings", settings.model_dump())

    def set_worker_heartbeat(self) -> None:
        self._set("worker_heartbeat", datetime.now(UTC).isoformat())

    def get_worker_heartbeat(self) -> datetime | None:
        raw = self._get("worker_heartbeat")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            return None

    def set_maintenance_mode(self, *, enabled: bool) -> None:
        self._set("maintenance_mode", bool(enabled))

    def get_maintenance_mode(self) -> bool:
        return bool(self._get("maintenance_mode"))

    def get_backup_status(self) -> dict[str, dict[str, object]] | None:
        # Read per-leg sentinel JSON files from the shared backup volume (NOT the DB, so a restore
        # can't roll status back). Missing dir / unreadable leg -> omit that leg; None if no dir.
        import json
        from pathlib import Path

        base = Path(self._backup_status_dir) if self._backup_status_dir else None
        if base is None or not base.is_dir():
            return None
        out: dict[str, dict[str, object]] = {}
        for leg in ("files", "pg", "offsite", "drill"):
            f = base / f"{leg}.json"
            if not f.is_file():
                continue
            try:
                out[leg] = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def get_backup_history(
        self, limit: int = 100, leg: str | None = None
    ) -> tuple[list[dict[str, object]], bool, bool, bool]:
        # Tail-read the host-written append-only history.jsonl (outside the DB, like the sentinels).
        # Bounded read (cap the bytes we touch, NOT just the lines, so a corrupt/huge file can't OOM
        # or stall the request), JSONL-parse skipping malformed lines, filter by leg, newest-first.
        # Never raises: a missing/corrupt/empty file degrades to ([], False, False, True).
        import hashlib
        import json
        from pathlib import Path

        read_cap = 256 * 1024  # bytes; an upper bound on how much of the tail we parse
        base = Path(self._backup_status_dir) if self._backup_status_dir else None
        if base is None:
            return ([], False, False, True)
        path = base / "history.jsonl"
        try:
            if not path.is_file():
                return ([], False, False, True)
            size = path.stat().st_size
            truncated = size > read_cap
            with path.open("rb") as fh:
                if truncated:
                    fh.seek(size - read_cap)
                    fh.readline()  # drop the partial first line after seeking mid-file
                raw = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return ([], False, False, True)

        lines = [ln for ln in raw.splitlines() if ln.strip()]
        if not lines:
            # An empty file means the source exists but has no events yet.
            return ([], False, truncated, True)

        # Verify the prev_sha256 chain across the window (best-effort). Each line's prev_sha256 must
        # equal the sha256 of the preceding line; a mismatch -> the history was tampered/truncated.
        integrity_ok = True
        for i in range(1, len(lines)):
            try:
                claimed = json.loads(lines[i]).get("prev_sha256", "")
            except json.JSONDecodeError:
                integrity_ok = False
                continue
            actual = hashlib.sha256(lines[i - 1].encode("utf-8")).hexdigest()
            if claimed != actual:
                integrity_ok = False
                break

        events: list[dict[str, object]] = []
        for ln in lines:
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            if leg is not None and rec.get("leg") != leg:
                continue
            events.append(rec)
        events.reverse()  # newest-first
        return (events[: max(0, limit)], True, truncated, integrity_ok)


_PROJECTION_HEADER_COLS = "algorithm, version, input_fingerprint, n_points, truncated, computed_at"


class PostgresEmbeddingProjectionRepository:
    """Caches one 2D/3D embedding-space projection per (tenant, dim) (ADR-0016)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, projection: EmbeddingProjection) -> None:
        projection_id = uuid.uuid4().hex
        with self._db.connection() as conn, conn.transaction():
            # Replace any existing projection for this (tenant, dim); cascade drops its points.
            conn.execute(
                "DELETE FROM embedding_projections WHERE tenant_id=%s AND dim=%s",
                (projection.tenant_id, projection.dim),
            )
            conn.execute(
                "INSERT INTO embedding_projections "
                "(id, tenant_id, dim, algorithm, version, input_fingerprint, n_points, "
                "truncated, computed_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    projection_id,
                    projection.tenant_id,
                    projection.dim,
                    projection.algorithm,
                    projection.version,
                    projection.input_fingerprint,
                    projection.n_points,
                    projection.truncated,
                    projection.computed_at,
                ),
            )
            # COPY the points in one stream - projections can hold tens of thousands of rows.
            with (
                conn.cursor() as cur,
                cur.copy(
                    "COPY embedding_projection_points "
                    "(projection_id, tenant_id, chunk_id, document_id, x, y, z, cluster) FROM STDIN"
                ) as copy,
            ):
                for p in projection.points:
                    copy.write_row(
                        (
                            projection_id,
                            projection.tenant_id,
                            p.chunk_id,
                            p.document_id,
                            p.x,
                            p.y,
                            p.z,
                            p.cluster,
                        )
                    )

    def get(self, tenant_id: str, dim: int) -> EmbeddingProjection | None:
        with self._db.connection() as conn:
            header = conn.execute(
                f"SELECT id, {_PROJECTION_HEADER_COLS} FROM embedding_projections "
                "WHERE tenant_id=%s AND dim=%s",
                (tenant_id, dim),
            ).fetchone()
            if header is None:
                return None
            rows = conn.execute(
                "SELECT chunk_id, document_id, x, y, z, cluster FROM embedding_projection_points "
                "WHERE projection_id=%s",
                (header[0],),
            ).fetchall()
        points = [
            ProjectionPoint(chunk_id=r[0], document_id=r[1], x=r[2], y=r[3], z=r[4], cluster=r[5])
            for r in rows
        ]
        return self._row_to_projection(tenant_id, dim, header, points)

    def get_header(self, tenant_id: str, dim: int) -> EmbeddingProjection | None:
        with self._db.connection() as conn:
            header = conn.execute(
                f"SELECT id, {_PROJECTION_HEADER_COLS} FROM embedding_projections "
                "WHERE tenant_id=%s AND dim=%s",
                (tenant_id, dim),
            ).fetchone()
        if header is None:
            return None
        return self._row_to_projection(tenant_id, dim, header, [])

    @staticmethod
    def _row_to_projection(
        tenant_id: str, dim: int, header: tuple[Any, ...], points: list[ProjectionPoint]
    ) -> EmbeddingProjection:
        _id, algorithm, version, fingerprint, n_points, truncated, computed_at = header
        return EmbeddingProjection(
            tenant_id=tenant_id,
            dim=dim,
            algorithm=algorithm,
            version=version,
            input_fingerprint=fingerprint,
            n_points=n_points,
            truncated=truncated,
            computed_at=computed_at,
            points=points,
        )


class PostgresProjectionRequestRepository:
    """DB-backed recompute queue for embedding projections (ADR-0016, M7.1)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def request(self, tenant_id: str) -> None:
        # One live request per tenant: a repeat press while one is pending/running is a no-op.
        with self._db.connection() as conn:
            conn.execute(
                "INSERT INTO projection_requests (id, tenant_id, status) "
                "VALUES (%s, %s, 'pending') "
                "ON CONFLICT (tenant_id) DO NOTHING",
                (uuid.uuid4().hex, tenant_id),
            )

    def has_pending(self, tenant_id: str) -> bool:
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM projection_requests WHERE tenant_id=%s", (tenant_id,)
            ).fetchone()
        return row is not None

    def claim_next(self) -> ProjectionRequest | None:
        # Claim the oldest pending request; SKIP LOCKED stops two workers grabbing the same one.
        with self._db.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT id, tenant_id, requested_at FROM projection_requests "
                "WHERE status='pending' ORDER BY requested_at FOR UPDATE SKIP LOCKED LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE projection_requests SET status='running', claimed_at=now() WHERE id=%s",
                (row[0],),
            )
        return ProjectionRequest(id=row[0], tenant_id=row[1], requested_at=row[2], status="running")

    def complete(self, request_id: str) -> None:
        with self._db.connection() as conn:
            conn.execute("DELETE FROM projection_requests WHERE id=%s", (request_id,))


class PostgresChatThreadRepository:
    """``ChatThreadRepository`` over PostgreSQL. Tenant-scoped; messages cascade with the thread."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create_thread(self, tenant_id: str, title: str = "") -> ChatThread:
        thread_id = uuid.uuid4().hex
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            row = cur.execute(
                "INSERT INTO chat_threads (id, tenant_id, title) VALUES (%s, %s, %s) "
                "RETURNING id, title, title_source, created_at, updated_at",
                (thread_id, tenant_id, title),
            ).fetchone()
        assert row is not None
        return ChatThread(
            id=row["id"],
            title=row["title"],
            title_source=row["title_source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            message_count=0,
        )

    # Per-thread token total summed from each assistant message's metrics jsonb (M8 #11).
    _TOKENS_SUM = (
        "COALESCE(SUM("
        "COALESCE((m.metrics->>'prompt_tokens')::int,0)"
        "+COALESCE((m.metrics->>'answer_tokens')::int,0)"
        "+COALESCE((m.metrics->>'reasoning_tokens')::int,0)"
        "+COALESCE((m.metrics->>'overhead_tokens')::int,0)),0)"
    )
    _MS_SUM = "COALESCE(SUM(COALESCE((m.metrics->>'total_ms')::int,0)),0)"

    def list_threads(self, tenant_id: str, *, limit: int = 50) -> list[ChatThread]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                "SELECT t.id, t.title, t.title_source, t.created_at, t.updated_at, "
                "(SELECT count(*) FROM chat_messages m WHERE m.thread_id = t.id) AS message_count, "
                f"(SELECT {self._TOKENS_SUM} FROM chat_messages m WHERE m.thread_id = t.id) "
                "AS total_tokens, "
                f"(SELECT {self._MS_SUM} FROM chat_messages m WHERE m.thread_id = t.id) "
                "AS total_inference_ms "
                "FROM chat_threads t WHERE t.tenant_id=%s ORDER BY t.updated_at DESC LIMIT %s",
                (tenant_id, limit),
            ).fetchall()
        return [
            ChatThread(
                id=r["id"],
                title=r["title"],
                title_source=r["title_source"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                message_count=r["message_count"],
                total_tokens=r["total_tokens"],
                total_inference_ms=r["total_inference_ms"],
            )
            for r in rows
        ]

    @staticmethod
    def _to_message(r: dict[str, Any]) -> ChatMessage:
        metrics = r.get("metrics") or None
        return ChatMessage(
            id=r["id"],
            role=r["role"],
            content=r["content"],
            created_at=r["created_at"],
            reasoning=r["reasoning"] or "",
            citations=[Citation(**c) for c in (r["citations"] or [])],
            ranking=[RankedChunk(**rc) for rc in (r.get("ranking") or [])],
            metrics=TurnMetrics(**metrics) if metrics else None,
        )

    def get_messages(self, tenant_id: str, thread_id: str) -> list[ChatMessage]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                "SELECT id, role, content, created_at, reasoning, citations, ranking, metrics "
                "FROM chat_messages WHERE tenant_id=%s AND thread_id=%s ORDER BY created_at, id",
                (tenant_id, thread_id),
            ).fetchall()
        return [self._to_message(r) for r in rows]

    def append_message(
        self,
        tenant_id: str,
        thread_id: str,
        role: str,
        content: str,
        *,
        reasoning: str = "",
        citations: list[Citation] | None = None,
        ranking: list[RankedChunk] | None = None,
        metrics: TurnMetrics | None = None,
    ) -> ChatMessage:
        message_id = uuid.uuid4().hex
        citation_json = Json([c.model_dump() for c in (citations or [])])
        ranking_json = Json([rc.model_dump() for rc in (ranking or [])])
        metrics_json = Json(metrics.model_dump() if metrics is not None else {})
        with self._db.connection() as conn, conn.transaction():
            cur = conn.cursor(row_factory=dict_row)
            row = cur.execute(
                "INSERT INTO chat_messages "
                "(id, thread_id, tenant_id, role, content, reasoning, citations, ranking, metrics) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id, role, content, created_at, reasoning, citations, ranking, metrics",
                (
                    message_id,
                    thread_id,
                    tenant_id,
                    role,
                    content,
                    reasoning,
                    citation_json,
                    ranking_json,
                    metrics_json,
                ),
            ).fetchone()
            # Bump the thread's activity time; seed the title from this message only while it is
            # still auto (a manual rename sets title_source='manual' and is never overwritten).
            conn.execute(
                "UPDATE chat_threads SET updated_at = now(), "
                "title = CASE WHEN title_source = 'auto' AND title = '' "
                "THEN left(%s, 80) ELSE title END "
                "WHERE id=%s AND tenant_id=%s",
                (content, thread_id, tenant_id),
            )
        assert row is not None
        return self._to_message(row)

    def thread_exists(self, tenant_id: str, thread_id: str) -> bool:
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM chat_threads WHERE id=%s AND tenant_id=%s", (thread_id, tenant_id)
            ).fetchone()
        return row is not None

    def delete_thread(self, tenant_id: str, thread_id: str) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "DELETE FROM chat_threads WHERE id=%s AND tenant_id=%s", (thread_id, tenant_id)
            )

    def update_title(self, tenant_id: str, thread_id: str, title: str) -> ChatThread | None:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            row = cur.execute(
                "UPDATE chat_threads SET title=%s, title_source='manual' "
                "WHERE id=%s AND tenant_id=%s "
                "RETURNING id, title, title_source, created_at, updated_at, "
                "(SELECT count(*) FROM chat_messages m WHERE m.thread_id = chat_threads.id) "
                "AS message_count",
                (title, thread_id, tenant_id),
            ).fetchone()
        if row is None:
            return None
        return ChatThread(
            id=row["id"],
            title=row["title"],
            title_source=row["title_source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            message_count=row["message_count"],
        )

    def delete_messages_from(self, tenant_id: str, thread_id: str, message_id: str) -> int:
        with self._db.connection() as conn, conn.transaction():
            cur = conn.cursor(row_factory=dict_row)
            target = cur.execute(
                "SELECT created_at FROM chat_messages "
                "WHERE id=%s AND thread_id=%s AND tenant_id=%s",
                (message_id, thread_id, tenant_id),
            ).fetchone()
            if target is None:
                return 0
            # Delete the target + everything at/after it chronologically (id breaks ties).
            cur.execute(
                "DELETE FROM chat_messages WHERE thread_id=%s AND tenant_id=%s "
                "AND (created_at, id) >= (%s, %s)",
                (thread_id, tenant_id, target["created_at"], message_id),
            )
            removed = cur.rowcount
            conn.execute(
                "UPDATE chat_threads SET updated_at = now() WHERE id=%s AND tenant_id=%s",
                (thread_id, tenant_id),
            )
        return removed
