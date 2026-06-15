"""Contracts-first data schemas for DokTok NG.

These Pydantic models are the canonical shapes for documents, ingestion jobs, chunks, entities,
artifacts, and audit events. They are defined in the contracts package so that core logic and all
adapters depend on the same shapes. See docs/architecture and brief sections 14-16.

Every tenant-owned entity carries a ``tenant_id`` (ADR-0007). Tenant identity always comes from the
authenticated token, never from request input (ADR-0008).
"""

from __future__ import annotations

from dataclasses import dataclass
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
    # Enrichment abstain marker (M7.3, ADR-0017): True = the document is unidentifiable (extraction
    # succeeded but the content is not meaningful), False = identifiable, None = not yet assessed.
    unidentifiable: bool | None = None
    # For DocumentStatus.DUPLICATE: the id of the already-ingested document this duplicates.
    duplicate_of: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentListPage(BaseModel):
    """A keyset-paginated page of documents, ordered (created_at DESC, id DESC).

    ``next_cursor`` is an opaque token for the next (older) page; ``None`` means this is the last
    page. ``total`` is the count for the active filters (exact at this scale; may become approximate
    for very large corpora). Keyset paging is correct under the live 4s poll: unlike offset, inserts
    and deletes elsewhere cannot make a page skip or duplicate rows.
    """

    items: list[Document] = Field(default_factory=list)
    total: int = 0
    next_cursor: str | None = None


class DocumentSort(StrEnum):
    """Sort key for the document list. ``acquired`` = when it entered the system (created_at);
    ``created`` = the document's own date (document_date)."""

    ACQUIRED = "acquired"
    CREATED = "created"
    TITLE = "title"
    CATEGORY = "category"


class SortDir(StrEnum):
    ASC = "asc"
    DESC = "desc"


class TokenMatch(StrEnum):
    """How multiple token filters combine: ANY = OR, ALL = AND (the document must carry all)."""

    ANY = "any"
    ALL = "all"


@dataclass(frozen=True)
class ListAnchor:
    """Keyset cursor anchor for the document list: the sort value + unique id of the last row seen.

    It carries the ``sort``/``direction`` it was produced for so a cursor is self-describing and
    can be rejected if replayed against a different ordering. ``value`` is the row's value for the
    chosen sort column (a datetime for ``acquired``, a date for ``created``, a string for
    ``title``/``category``, or ``None`` when that column is null - null rows always sort last).
    """

    sort: DocumentSort
    direction: SortDir
    value: datetime | date | str | None
    doc_id: str


class DocumentIdSelection(BaseModel):
    """All document ids matching a filter, for 'select all matching' bulk actions.

    Capped: when more than the cap match, ``ids`` holds the first ``cap`` and ``truncated`` is
    true, signalling the client to act on the filter server-side rather than shipping an id list.
    """

    ids: list[str] = Field(default_factory=list)
    total: int = 0
    truncated: bool = False


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
    # Importance of this source (M6.4): 0..1, 1.0 = most relevant, from the reranker's final order
    # (normalized rank). None = unscored. The UI shows it as a bar/percent per source.
    relevance: float | None = None


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


class ChatTurn(BaseModel):
    """One prior message in a chat conversation (ADR-0018). Used to rewrite follow-ups."""

    role: str  # "user" | "assistant"
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    # Bound the free-text question: a non-empty, sanely-sized prompt (a multi-MB body would be a
    # cheap resource-exhaustion vector and can overflow the model context).
    question: str = Field(min_length=1, max_length=4000)
    # Prior conversation turns (multi-turn, ADR-0018); empty = single-turn (current behaviour).
    # Bounded so the rewrite prompt can't be flooded; the answerer keeps only the most recent turns.
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)
    # Server-side thread (M6.4 #248): when set, history is loaded from the DB and this turn is
    # persisted to the thread; `history` above is ignored. None = stateless (client-held history).
    thread_id: str | None = None
    limit: int = Field(default=8, ge=1, le=20)
    # Per-request reasoning override (streaming only). None = follow the configured `rag.reasoning`
    # (Settings > AI > Document interrogation); True/False explicitly overrides it for this turn.
    # Enabling reasoning makes the answer noticeably slower (the model thinks before answering).
    reasoning: bool | None = None


class ChatMessage(BaseModel):
    """One persisted message in a chat thread (M6.4 #248)."""

    id: str
    role: str  # "user" | "assistant"
    content: str
    created_at: datetime
    # Persisted so a resumed/reloaded thread can re-show the model's reasoning and the source cards
    # (assistant turns only; empty for user turns).
    reasoning: str = ""
    citations: list[Citation] = Field(default_factory=list)


class ChatThread(BaseModel):
    """A persisted conversation (M6.4 #248). ``title`` is derived from the first user message."""

    id: str
    title: str = ""
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class QueryFilters(BaseModel):
    """Retrieval filters inferred from a chat question (M6.4 Phase 2, ADR-0018).

    Scope the hybrid retriever to a document category and/or document-date range, e.g. "what did the
    2023 invoices say about late fees" -> category="invoice", date range across 2023.
    """

    category: str | None = None
    date_from: date | None = None
    date_to: date | None = None

    def is_empty(self) -> bool:
        return self.category is None and self.date_from is None and self.date_to is None


class RagAnswer(BaseModel):
    """A grounded answer with citations (brief section 18)."""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    grounded: bool
    # Standalone query a follow-up was rewritten to (multi-turn, ADR-0018); None = not rewritten.
    rewritten_query: str | None = None
    # Filters inferred from the question + applied to retrieval (M6.4 Phase 2); None = none applied.
    filters: QueryFilters | None = None


class ChatEvent(BaseModel):
    """One event in a streamed chat turn (M6.4, ADR-0018 Phase 3). ``type`` is one of meta / step /
    reasoning / token / sources / done / error; the relevant field(s) are set per type. ``step`` is
    a human-readable pipeline-phase label (understanding / searching / answering) for the activity
    window - the deterministic-RAG analogue of tool-call traces."""

    type: str
    delta: str = ""  # reasoning / token / step (the step label)
    rewritten_query: str | None = None  # meta
    filters: QueryFilters | None = None  # meta (inferred retrieval filters, M6.4 Phase 2)
    citations: list[Citation] = Field(default_factory=list)  # sources
    grounded: bool = False  # done
    message: str = ""  # error


class DocumentContent(BaseModel):
    """The canonical extracted text of a document (content.md)."""

    document_id: str
    content: str


class LayoutLine(BaseModel):
    """One OCR line + its bbox in the page image's pixels (x0,y0 top-left; x1,y1 bottom-right)."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float


class LayoutPage(BaseModel):
    """A page's OCR geometry: image pixel size + render DPI + the recognized line boxes."""

    page_number: int
    width_px: int
    height_px: int
    dpi: int | None = None
    lines: list[LayoutLine] = Field(default_factory=list)


class DocumentLayout(BaseModel):
    """Per-page OCR boxes for overlaying on the page image (empty until a doc is OCR'd)."""

    document_id: str
    pages: list[LayoutPage] = Field(default_factory=list)


class StatsSummary(BaseModel):
    """At-a-glance tenant counts for the overview dashboard."""

    documents: int
    jobs: dict[str, int] = Field(default_factory=dict)
    entities: int
    pending_ingest: int = 0  # files waiting in the ingest folder (no job yet)
    documents_pending_features: int = (
        0  # documents needing attention: >=1 FAILED feature (not merely in-progress) - M7.3
    )


class HealthStatus(BaseModel):
    """Backend health/status payload returned by GET /health."""

    status: str
    service: str
    version: str
    environment: str


class EntityTypeCount(BaseModel):
    """How many entities of a given type a document has (for the detail card's entity rollup)."""

    entity_type: str
    count: int


class DocumentEntitySummary(BaseModel):
    """A compact entity rollup for the document card; the full list is fetched on demand."""

    total: int = 0
    by_type: list[EntityTypeCount] = Field(default_factory=list)
    top: list[DocumentEntity] = Field(default_factory=list)


class DocumentContentMeta(BaseModel):
    """Extracted-text metadata: total length + a short excerpt (full text fetched on demand)."""

    length: int = 0
    excerpt: str = ""


class DocumentDetail(BaseModel):
    """One-round-trip aggregate for the document detail view's eager fold (review follow-up).

    Bundles everything shown immediately (identity, summary, processing, categories, an entity
    summary, a content excerpt, and recent activity) so the card needs one request instead of six.
    The two unbounded payloads - the full extracted text and the full entity list - stay behind the
    existing lazy endpoints and are only fetched when their tab is opened.
    """

    document: Document
    features: list[DocumentFeature] = Field(default_factory=list)
    categories: list[Category] = Field(default_factory=list)
    entities: DocumentEntitySummary = Field(default_factory=DocumentEntitySummary)
    content: DocumentContentMeta = Field(default_factory=DocumentContentMeta)
    recent_activity: list[AuditEvent] = Field(default_factory=list)


class AiPurposeSettings(BaseModel):
    """Model choice for one AI purpose (the data pipeline, or document interrogation)."""

    provider: str = "ollama"  # 'ollama' | 'openai'
    model: str
    num_ctx: int
    reasoning: str = "off"  # 'off' | 'low' | 'medium' | 'high' (ignored by non-reasoning models)


def _default_pipeline() -> AiPurposeSettings:
    return AiPurposeSettings(provider="ollama", model="qwen3:14b", num_ctx=8192)


def _default_rag() -> AiPurposeSettings:
    return AiPurposeSettings(provider="ollama", model="qwen3.6:35b-a3b", num_ctx=32768)


class AiSettings(BaseModel):
    """The configurable AI model selection (Settings tab > AI section). Applied on restart."""

    pipeline: AiPurposeSettings = Field(default_factory=_default_pipeline)
    rag: AiPurposeSettings = Field(default_factory=_default_rag)


class OcrSettings(BaseModel):
    """OCR processing settings (Settings tab > OCR, M7.6).

    ``ocr_concurrency`` is the number of OCR pages processed in parallel and sizes the PaddleOCR
    worker-process pool. The worker live-reloads it (no restart) between ingest scans.
    """

    ocr_concurrency: int = Field(default=4, ge=1, le=32)


class AiSettingsResponse(AiSettings):
    """AI settings as returned to the UI - never the OpenAI key, only whether one is set."""

    openai_api_key_set: bool = False
    # Read-only: the embedding model + its context, shown so the user can see what indexes their
    # corpus. Not user-selectable - changing it would need a vector-dimension migration + re-index.
    embedding_model: str = ""
    embedding_num_ctx: int = 0


class AiSettingsUpdate(AiSettings):
    """AI settings update from the UI. ``openai_api_key`` is write-only: None leaves it unchanged,
    "" clears it, a value sets it."""

    openai_api_key: str | None = None


class ModelOption(BaseModel):
    """A selectable model for a purpose, with its allowed context sizes."""

    provider: str
    model: str
    label: str
    contexts: list[int]
    supports_reasoning: bool = False


class ModelCatalog(BaseModel):
    """The models the Settings UI offers per purpose + the reasoning-density levels."""

    pipeline: list[ModelOption] = Field(default_factory=list)
    rag: list[ModelOption] = Field(default_factory=list)
    reasoning_levels: list[str] = Field(default_factory=list)


class VizLegendEntry(BaseModel):
    """One category and its assigned color in the embedding-map legend (ADR-0016, M7.1)."""

    category: str
    color: str


class VizPoint(BaseModel):
    """A chunk rendered on the embedding map: coordinates + its color category + a text snippet."""

    chunk_id: str
    document_id: str
    x: float
    y: float
    z: float | None = None
    category: str
    cluster: int | None = None
    snippet: str


class ProjectionMeta(BaseModel):
    """Metadata describing the cached projection a map was built from (ADR-0016, M7.1)."""

    dim: int
    algorithm: str
    version: int
    computed_at: datetime
    n_points: int
    truncated: bool
    stale: bool


class EmbeddingMap(BaseModel):
    """The full embedding-map payload for one dimension: points + legend + projection metadata.

    ``computed`` is False when no projection has been built yet; ``recompute_pending`` is True while
    a recompute is queued or running, so the UI can show a busy state.
    """

    dim: int
    computed: bool
    recompute_pending: bool = False
    points: list[VizPoint] = Field(default_factory=list)
    legend: list[VizLegendEntry] = Field(default_factory=list)
    meta: ProjectionMeta | None = None


class ProjectionDimStatus(BaseModel):
    """Cache state of one dimension's projection (for the status endpoint)."""

    dim: int
    computed: bool
    stale: bool
    n_points: int = 0
    computed_at: datetime | None = None


class ProjectionStatus(BaseModel):
    """Whether a recompute is in flight and the per-dimension cache state (ADR-0016, M7.1)."""

    recompute_pending: bool
    dims: list[ProjectionDimStatus] = Field(default_factory=list)


class ProjectionRequest(BaseModel):
    """A pending request to recompute a tenant's embedding projections (ADR-0016, M7.1).

    The API enqueues one; the worker claims it, fits the 2D and 3D projections, writes the cache,
    and clears it. There is no message broker, so this DB row is the queue.
    """

    id: str
    tenant_id: str
    requested_at: datetime
    status: str = "pending"


class ProjectionPoint(BaseModel):
    """One chunk placed in the reduced (2D/3D) embedding space (ADR-0016, M7.1).

    Geometry only; the point's color category and text snippet are resolved at read time from the
    live category links and chunk text, so re-classification does not require re-projection.
    """

    chunk_id: str
    document_id: str
    x: float
    y: float
    z: float | None = None
    cluster: int | None = None


class EmbeddingProjection(BaseModel):
    """A cached 2D/3D projection of a tenant's chunk embeddings for the Insights tab (ADR-0016).

    One cached projection per (tenant_id, dim); recompute replaces it. ``input_fingerprint``
    captures the inputs (chunk count + freshness + algorithm + version) so the UI can detect
    staleness without refitting. ``points`` is empty in header-only reads (status checks).
    """

    tenant_id: str
    dim: int
    algorithm: str
    version: int = 1
    input_fingerprint: str
    n_points: int
    truncated: bool = False
    computed_at: datetime
    points: list[ProjectionPoint] = Field(default_factory=list)
