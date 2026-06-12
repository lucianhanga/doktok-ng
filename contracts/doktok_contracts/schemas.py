"""Contracts-first data schemas for DokTok NG.

These Pydantic models are the canonical shapes for documents, ingestion jobs, chunks, entities,
artifacts, and audit events. They are defined in the contracts package so that core logic and all
adapters depend on the same shapes. See docs/architecture and brief sections 14-16.

Every tenant-owned entity carries a ``tenant_id`` (ADR-0007). Tenant identity always comes from the
authenticated token, never from request input (ADR-0008).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    """Ingestion job state machine (ADR-0004)."""

    QUEUED = "queued"
    DETECTING = "detecting"
    HASHING = "hashing"
    NORMALIZING = "normalizing"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    ACTIVATING = "activating"
    ACTIVE = "active"
    FAILED = "failed"
    QUARANTINED = "quarantined"
    DUPLICATE = "duplicate"


class DocumentStatus(StrEnum):
    PROCESSING = "processing"
    ACTIVE = "active"
    FAILED = "failed"
    QUARANTINED = "quarantined"
    DUPLICATE = "duplicate"


class SecurityDecision(StrEnum):
    """Outcome of evaluating an ingested file against the security policy."""

    ALLOW = "allow"
    QUARANTINE = "quarantine"
    REJECT = "reject"


class AuditEventType(StrEnum):
    """Controlled vocabulary for the immutable activity/audit trail (ADR-0006)."""

    DOCUMENT_RECEIVED = "document.received"
    DOCUMENT_IDENTIFIED = "document.identified"
    DOCUMENT_ACTIVATED = "document.activated"
    DOCUMENT_QUARANTINED = "document.quarantined"
    DOCUMENT_FAILED = "document.failed"
    DOCUMENT_DUPLICATE = "document.duplicate"


class EntityType(StrEnum):
    PERSON = "PERSON"
    ORG = "ORG"
    GPE = "GPE"
    LOCATION = "LOCATION"
    DATE = "DATE"
    EMAIL = "EMAIL"
    URL = "URL"
    MONEY = "MONEY"
    DOCUMENT_ID = "DOCUMENT_ID"
    INVOICE_ID = "INVOICE_ID"
    CONTRACT_ID = "CONTRACT_ID"
    CUSTOM_TOKEN = "CUSTOM_TOKEN"


class TenantContext(BaseModel):
    """The authenticated caller's tenant (ADR-0008)."""

    tenant_id: str


class Document(BaseModel):
    id: str
    tenant_id: str
    current_version_id: str | None = None
    sha256: str
    original_filename: str
    detected_mime: str | None = None
    title: str | None = None
    status: DocumentStatus = DocumentStatus.PROCESSING
    storage_path: str | None = None
    created_at: datetime
    activated_at: datetime | None = None
    ingested_at: datetime | None = None
    # Enrichment (M6.2): document_date/location are None when undeterminable (UI shows "n/a").
    document_date: date | None = None
    location: str | None = None
    summary: str | None = None
    # For DocumentStatus.DUPLICATE: the id of the already-ingested document this duplicates.
    duplicate_of: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentVersion(BaseModel):
    id: str
    tenant_id: str
    document_id: str
    version_number: int
    sha256: str
    created_at: datetime
    extraction_method: str | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)


class IngestionJob(BaseModel):
    id: str
    tenant_id: str
    document_id: str | None = None
    source_path: str
    status: JobStatus = JobStatus.QUEUED
    detected_mime: str | None = None
    sha256: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentPage(BaseModel):
    id: str
    tenant_id: str
    document_id: str
    version_id: str
    page_number: int
    text: str
    layout: dict[str, Any] = Field(default_factory=dict)
    extraction_method: str | None = None
    ocr_confidence: float | None = None


class ChunkMetadata(BaseModel):
    document_id: str
    version_id: str
    chunk_id: str
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    source_offsets: dict[str, Any] | None = None
    extraction_method: str | None = None
    ocr_confidence: float | None = None
    token_count: int | None = None


class DocumentChunk(BaseModel):
    id: str
    tenant_id: str
    document_id: str
    version_id: str
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    text: str
    token_count: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentEntity(BaseModel):
    id: str
    tenant_id: str
    document_id: str
    version_id: str
    chunk_id: str | None = None
    entity_text: str
    entity_type: EntityType
    normalized_value: str | None = None
    frequency: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentArtifact(BaseModel):
    id: str
    tenant_id: str
    document_id: str
    version_id: str
    artifact_type: str
    storage_path: str
    mime_type: str | None = None
    sha256: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditEvent(BaseModel):
    id: str
    tenant_id: str
    event_type: str
    actor: str
    document_id: str | None = None
    job_id: str | None = None
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class EntitySummary(BaseModel):
    """A distinct entity for a tenant, with how widely it appears (brief section 17/19)."""

    entity_type: EntityType
    normalized_value: str
    document_count: int
    occurrences: int


class SearchHit(BaseModel):
    """A hybrid-search result (brief section 17)."""

    document_id: str
    chunk_id: str
    original_filename: str | None = None
    title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    snippet: str
    text: str = ""  # full chunk text (used as RAG context; the UI shows the snippet)
    score: float
    vector_score: float | None = None
    text_score: float | None = None


class Citation(BaseModel):
    """A source citation for a RAG answer (brief section 18)."""

    index: int
    document_id: str
    chunk_id: str
    original_filename: str | None = None
    title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    snippet: str


class FeatureStatus(StrEnum):
    """State of a single processing feature for one document (ADR-0009)."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class DocumentFeature(BaseModel):
    """Ledger row: how one processing feature has been applied to one document (ADR-0009)."""

    id: str
    tenant_id: str
    document_id: str
    feature: str
    feature_version: int = 1
    status: FeatureStatus = FeatureStatus.PENDING
    attempts: int = 0
    max_attempts: int = 3
    last_error: str | None = None
    last_attempt_at: datetime | None = None
    completed_at: datetime | None = None
    next_attempt_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TokenSuggestion(BaseModel):
    """An autocomplete suggestion for the faceted token search."""

    value: str
    document_count: int


class ExtractedRecord(BaseModel):
    """A structured line item extracted from a document for deterministic aggregation (M6.3)."""

    id: str
    tenant_id: str
    document_id: str
    record_type: str = "card_transaction"
    source_page: int | None = None
    raw_text: str
    occurred_on: date | None = None
    amount_minor: int | None = None  # integer minor units (cents)
    currency: str | None = None
    direction: str | None = None  # 'debit' | 'credit'
    merchant_raw: str | None = None
    merchant_normalized: str | None = None
    description: str | None = None
    account_label: str | None = None
    confidence: float = 1.0


class AggregationIntent(BaseModel):
    """A typed aggregation over extracted_records (M6.3) - the deterministic answer to questions
    like "how much did I spend at Block House" that top-k RAG cannot answer."""

    operation: str = "sum"  # 'sum' (of amount_minor) | 'count'
    record_type: str | None = None  # e.g. 'card_transaction'
    merchant: str | None = None  # fuzzy substring-matched against merchant_normalized
    direction: str | None = None  # 'debit' (spend) | 'credit' (refund/payment)
    currency: str | None = None  # ISO 4217, exact
    date_from: date | None = None
    date_to: date | None = None
    sample_limit: int = Field(default=10, ge=0, le=100)  # provenance rows to return


class AggregationBucket(BaseModel):
    """A per-currency rollup (money is never summed across currencies)."""

    currency: str | None = None
    total_minor: int = 0
    count: int = 0


class AggregationResult(BaseModel):
    """The result of an AggregationIntent: per-currency totals + sample rows for provenance."""

    operation: str
    count: int = 0
    by_currency: list[AggregationBucket] = Field(default_factory=list)
    samples: list[ExtractedRecord] = Field(default_factory=list)


class Category(BaseModel):
    """A controlled-vocabulary category for a tenant (M6.2, bounded to 20 active per tenant)."""

    id: str
    tenant_id: str
    name: str
    normalized: str
    status: str = "active"
    created_at: datetime | None = None


class CategorySummary(BaseModel):
    """A category with how many documents carry it (for the vocabulary list / filter)."""

    name: str
    document_count: int


class ChatRequest(BaseModel):
    # Bound the free-text question: a non-empty, sanely-sized prompt (a multi-MB body would be a
    # cheap resource-exhaustion vector and can overflow the model context).
    question: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=8, ge=1, le=20)


class RagAnswer(BaseModel):
    """A grounded answer with citations (brief section 18)."""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    grounded: bool


class DocumentContent(BaseModel):
    """The canonical extracted text of a document (content.md)."""

    document_id: str
    content: str


class StatsSummary(BaseModel):
    """At-a-glance tenant counts for the overview dashboard."""

    documents: int
    jobs: dict[str, int] = Field(default_factory=dict)
    entities: int
    pending_ingest: int = 0  # files waiting in the ingest folder (no job yet)
    documents_pending_features: int = (
        0  # documents with >=1 feature not done (pending/running/failed)
    )


class HealthStatus(BaseModel):
    """Backend health/status payload returned by GET /health."""

    status: str
    service: str
    version: str
    environment: str
