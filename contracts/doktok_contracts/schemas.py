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

from pydantic import BaseModel, Field, model_validator


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
    # Enrichment features driven by the reconciler (classify/NER/records/chunk_embed/...).
    FEATURE_COMPLETED = "feature.completed"
    FEATURE_FAILED = "feature.failed"
    # User-initiated lifecycle actions.
    FEATURE_RETRIED = "feature.retried"
    DOCUMENT_ROTATED = "document.rotated"
    DOCUMENT_REINGESTED = "document.reingested"
    DOCUMENT_DELETED = "document.deleted"
    DOCUMENT_VIEWED = "document.viewed"
    # System-level (non-document) events (M15 #373): configuration + service lifecycle.
    SETTINGS_CHANGED = "settings.changed"
    SERVICE_STARTED = "service.started"
    # Egress posture (no-egress gate). ENABLED = a settings change turned on remote egress (document
    # content will now leave the host - the auditable opt-in). BLOCKED = a runtime sink refused a
    # saved egress config because DOKTOK_NO_EGRESS is on (defense-in-depth, surfaced not silent).
    EGRESS_ENABLED = "egress.enabled"
    EGRESS_BLOCKED = "egress.blocked"
    # Backup / disaster-recovery events mirrored into the activity log from the host-written,
    # outside-Postgres append-only history (M12 DRP hardening). The authoritative source is the
    # history.jsonl file; these rows are a non-authoritative, idempotent mirror of a read window.
    BACKUP_COMPLETED = "backup.completed"
    BACKUP_FAILED = "backup.failed"
    DRILL_COMPLETED = "drill.completed"
    # Portable restore lifecycle (M12 portable restore Phase 2). Restoring the whole system from an
    # uploaded archive is the most destructive operation in the app, so every step is audited.
    RESTORE_PREVIEWED = "restore.previewed"
    RESTORE_REQUESTED = "restore.requested"
    RESTORE_COMPLETED = "restore.completed"
    RESTORE_FAILED = "restore.failed"


class EntityType(StrEnum):
    PERSON = "PERSON"
    ORG = "ORG"
    GPE = "GPE"
    LOCATION = "LOCATION"
    EMAIL = "EMAIL"
    URL = "URL"
    CUSTOM_TOKEN = "CUSTOM_TOKEN"
    # No longer extracted (M8.x, #312): the regex matches were mostly noise on real documents.
    # Kept in the vocabulary so historical rows + the cleanup migration still resolve.
    DATE = "DATE"
    MONEY = "MONEY"
    DOCUMENT_ID = "DOCUMENT_ID"
    INVOICE_ID = "INVOICE_ID"
    CONTRACT_ID = "CONTRACT_ID"


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
    # Per-document processing summaries for the list tooltip, keyed by document id (sidecar map so
    # the shared ``Document`` shape stays unchanged for search). Populated only on the list
    # response; done/failed counts come from one batched GROUP BY over this page's ids (no per-row).
    processing: dict[str, ProcessingSummary] = Field(default_factory=dict)


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
    # Enhanced activity log (full lifecycle). Defaults keep older call sites valid.
    severity: str = "info"  # info | warning | error
    phase: str = ""  # lifecycle phase: intake/extract/enrich/index/reconcile/user/delete
    description: str = ""  # human-readable one-line summary for the activity table
    actor_kind: str = "worker"  # worker | user | system
    record_kind: str | None = None  # which related record changed (metadata/category/entity/...)
    record_id: str | None = None
    # Document identity snapshot (captured at write time) so a row stays readable after the
    # document is deleted - the activity row has no FK and survives the document's cascade delete.
    doc_filename: str | None = None
    doc_title: str | None = None


class EntitySummary(BaseModel):
    """A distinct entity for a tenant, with how widely it appears (brief section 17/19)."""

    entity_type: EntityType
    normalized_value: str
    document_count: int
    occurrences: int


class KgEntity(BaseModel):
    """A canonical cross-document entity node in the knowledge graph (KAG Phase 1).

    One node per distinct ``(tenant_id, entity_type, normalized_value)``. The ``id`` is a
    deterministic uuid5 of exactly that triple (see ``knowledge_graph.resolve``), so the same
    normalized entity in two documents resolves to the same node with no clustering.
    """

    id: str
    tenant_id: str
    entity_type: EntityType
    normalized_value: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class KgEntityMention(BaseModel):
    """Links one ``document_entities`` mention to its canonical ``KgEntity`` node, with provenance.

    ``mention_id`` is the ``document_entities`` row id (the mention's identity, also the PK in
    storage), so resolution is idempotent: re-running a document replaces its mention rows in place.
    """

    mention_id: str
    tenant_id: str
    canonical_entity_id: str
    document_id: str
    chunk_id: str | None = None
    entity_type: EntityType
    normalized_value: str


class KgEdge(BaseModel):
    """A canonical directed relation triple between two entity nodes (KAG Phase 2).

    One row per distinct ``(tenant_id, src_entity_id, predicate, dst_entity_id)``. The ``id`` is a
    deterministic uuid5 of that quad (see ``knowledge_graph.predicates.canonical_edge_id``).
    ``evidence_count`` is a denormalized count of ``KgEdgeProvenance`` rows for this edge.
    """

    id: str  # canonical_edge_id(tenant, src, predicate, dst)
    tenant_id: str
    src_entity_id: str
    predicate: str
    dst_entity_id: str
    evidence_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class KgEdgeProvenance(BaseModel):
    """Per-extraction evidence for one edge: document/chunk + verbatim span."""

    id: str
    tenant_id: str
    edge_id: str
    document_id: str
    chunk_id: str | None = None
    evidence: str


class AliasFold(BaseModel):
    """A decision to fold one node (the alias) into another (the canonical), KAG alias tier.

    Produced by ``knowledge_graph.alias.compute_alias_folds`` (pure domain logic) and applied
    transactionally by ``KnowledgeGraphRepository.resolve_aliases``: the alias node's mentions and
    edges are re-pointed to ``canonical_id``, an alias row records the mapping so the merge survives
    re-ingestion, and the alias node is deleted.
    """

    alias_id: str  # the canonical_entity_id of the node being folded away
    alias_type: str  # entity_type (folds never cross types)
    alias_normalized: str  # the folded node's normalized_value (the alias-table key)
    canonical_id: str  # the surviving canonical node's id


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


class GraphTriple(BaseModel):
    """One grounded relationship surfaced by graph retrieval (KAG Phase 3).

    Display labels are the canonical nodes' normalized values; ``document_id``/``chunk_id`` tie the
    relationship to the source evidence so it can be cited with the same [n] as its excerpt.
    """

    subject: str
    predicate: str
    object: str
    document_id: str = ""
    chunk_id: str | None = None
    evidence: str = ""


class GraphRetrieval(BaseModel):
    """Result of a ``GraphRetriever`` (KAG Phase 3): chunk-grounded ``hits`` that fuse into the
    hybrid candidate pool (reranked + cited like any retrieval result) plus the compact relationship
    ``triples`` injected into the grounded prompt as a scaffold. Both empty = no graph signal for
    this question (gate missed, nothing linked, or no edges) - the caller behaves exactly as today.
    """

    hits: list[SearchHit] = Field(default_factory=list)
    triples: list[GraphTriple] = Field(default_factory=list)


class RankedChunk(BaseModel):
    """One candidate chunk in a turn's retrieval/ranking trace (M8 #4/#7). The reranker returns an
    order, not a score, so ``relevance`` is the normalized rank (best=1.0) and ``retrieval_score``
    is the only true numeric (the hybrid retriever's RRF score)."""

    chunk_id: str
    document_id: str
    original_filename: str | None = None
    page_start: int | None = None
    retrieval_score: float  # hybrid (RRF) score from the retriever
    relevance: float | None = None  # normalized final rank (selected chunks only)
    selected: bool = False  # made the final top-k packed into the prompt
    cited: bool = False  # the answer actually referenced it with [n]


class TurnMetrics(BaseModel):
    """Per-assistant-turn LLM token/timing metrics (M8 #2/#3/#11). ``reasoning_tokens`` powers the
    collapsed Reasoning & Steps badge; ``overhead_tokens`` is the query-rewrite/filter call. Totals
    sum across all turns for the per-chat figure. Tokens may be estimated (``estimated``)."""

    prompt_tokens: int = 0  # the answer call's input tokens
    answer_tokens: int = 0  # the answer call's visible output tokens
    reasoning_tokens: int = 0  # the answer call's thinking tokens
    overhead_tokens: int = 0  # the understanding (rewrite + filters) call, total tokens
    reasoning_ms: int = 0  # wall time until the first answer token (reasoning phase)
    answer_ms: int = 0  # wall time streaming the answer
    total_ms: int = 0  # whole turn
    reused_previous_results: bool = False  # the follow-up was rewritten using the conversation
    rewritten_query: str | None = None
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return (
            self.prompt_tokens + self.answer_tokens + self.reasoning_tokens + self.overhead_tokens
        )


class FeatureMetrics(BaseModel):
    """Per-feature-run telemetry persisted on the document_features ledger row's ``metrics`` jsonb
    (per-document processing telemetry). ``duration_ms`` is the reconciler's wall-clock around the
    processor; the token counts come from the enrichment/embedding model when it reports them (0
    when the feature does not call an LLM, e.g. thumbnail/extract). ``estimated`` marks a char-ratio
    estimate rather than a provider-reported count. Stored via ``model_dump()`` (migration 0029
    chat-metrics precedent); old rows default to an empty object -> all zeros."""

    duration_ms: int = 0
    prompt_tokens: int = 0
    answer_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    estimated: bool = False

    @model_validator(mode="after")
    def _fill_total(self) -> FeatureMetrics:
        # Mirror LlmUsage/TurnMetrics: derive the total from the parts when not given explicitly.
        if self.total_tokens == 0:
            self.total_tokens = self.prompt_tokens + self.answer_tokens
        return self


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
    # Per-run telemetry (duration + enrichment tokens). Empty default for rows written before the
    # metrics column / by features that do not measure (backward compatible).
    metrics: FeatureMetrics = Field(default_factory=FeatureMetrics)


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
    # Extraction confidence (0..1) or None = UNSCORED. The extractor does not emit a score today, so
    # new rows stay None until a model genuinely scores them (a 1.0 default would dishonestly read
    # as "100% confident" for never-scored rows). Migration 0032 backfilled legacy 1.0 rows to NULL.
    confidence: float | None = None


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
    # Chat mode (ADR-0022): "classic" = the deterministic RAG pipeline (default); "agent" = the
    # single-agent tool-calling loop; "multi" = the LangGraph plan/gather/merge/critic graph. The
    # agent/multi paths are opt-in and fall back to classic when the configured model can't do
    # tool-calling. A mis-behaving agent turn never affects the default path.
    agent_mode: str = "classic"


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
    # Per-turn ranking trace + token/timing metrics (M8); assistant turns only, defaults keep old
    # rows + user turns valid.
    ranking: list[RankedChunk] = Field(default_factory=list)
    metrics: TurnMetrics | None = None


class ChatThread(BaseModel):
    """A persisted conversation (M6.4 #248). ``title`` is derived from the first user message until
    the user renames it, after which ``title_source='manual'`` and auto-seeding stops."""

    id: str
    title: str = ""
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    title_source: str = "auto"  # 'auto' | 'manual'
    # Per-chat aggregates across all assistant turns (M8 #11), computed on read.
    total_tokens: int = 0
    total_inference_ms: int = 0


class ChatThreadUpdate(BaseModel):
    """Body for renaming a chat thread (M8 chat overhaul). Trimmed + bounded server-side."""

    title: str = Field(min_length=1, max_length=200)


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
    # Per-turn ranking trace + metrics (M8), so the one-shot path persists the same as streaming.
    ranking: list[RankedChunk] = Field(default_factory=list)
    metrics: TurnMetrics | None = None


class ChatEvent(BaseModel):
    """One event in a streamed chat turn (M6.4, ADR-0018 Phase 3). ``type`` is one of meta / step /
    reasoning / token / sources / ranking / metrics / done / error; relevant field(s) set per type.
    ``step`` is
    a human-readable pipeline-phase label (understanding / searching / answering) for the activity
    window - the deterministic-RAG analogue of tool-call traces."""

    type: str
    delta: str = ""  # reasoning / token / step (the step label)
    rewritten_query: str | None = None  # meta
    filters: QueryFilters | None = None  # meta (inferred retrieval filters, M6.4 Phase 2)
    citations: list[Citation] = Field(default_factory=list)  # sources
    grounded: bool = False  # done
    message: str = ""  # error
    ranking: list[RankedChunk] = Field(default_factory=list)  # ranking (M8 #4/#7)
    metrics: TurnMetrics | None = None  # metrics (M8 #2/#3/#11)


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
    documents_processing_features: int = (
        0  # documents with >=1 feature still queued/running (in-progress (re)processing)
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


class ProcessingStep(BaseModel):
    """One processing step in the per-document telemetry view: a feature run with its outcome,
    timing and (for LLM steps) token spend. Derived from a ``DocumentFeature`` ledger row + the
    catalog label; ``duration_ms``/tokens/``model`` come from the row's ``metrics`` (None/0 for rows
    processed before metrics existed or for non-LLM features)."""

    feature: str
    label: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    prompt_tokens: int | None = None
    answer_tokens: int | None = None
    total_tokens: int | None = None
    model: str | None = None
    estimated: bool = False
    attempts: int = 0
    last_error: str | None = None


class ProcessingTelemetry(BaseModel):
    """Per-document processing telemetry for the detail view: timestamps, extraction outcome, and a
    per-step breakdown with durations and enrichment token counts. Built from the document row's
    metadata + the already-loaded feature ledger rows (no extra query). Backward compatible:
    documents with empty metrics / no normalization yield nulls and zeros (today's look)."""

    received_at: datetime | None = None
    activated_at: datetime | None = None
    extraction_method: str = ""
    page_count: int | None = None
    ocr_outcome: str = "not_needed"  # "done" | "not_needed" | "failed"
    ocr_confidence: float | None = None
    normalized_from_mime: str = ""
    language: str = ""
    steps: list[ProcessingStep] = Field(default_factory=list)
    total_duration_ms: int = 0
    total_tokens: int = 0


class ProcessingSummary(BaseModel):
    """Compact per-document processing summary for the Documents list tooltip (set ONLY on list
    response items via the ``DocumentListPage.processing`` sidecar map - never on the shared
    ``Document`` shape used by search). The metadata-derived fields are free from the row; the
    done/failed counts come from one batched GROUP BY over the page's document ids (no N+1)."""

    extraction_method: str = ""
    ocr_outcome: str = "not_needed"  # "done" | "not_needed" | "failed"
    page_count: int | None = None
    normalized_from_mime: str = ""
    status: str = ""
    features_done: int = 0
    features_failed: int = 0


# Confidence-bucket thresholds for ConfidenceBuckets (shared by every record-summary backend so the
# in-memory and Postgres repositories agree). high >= HIGH; MEDIUM <= medium < HIGH; low < MEDIUM;
# a None confidence is UNSCORED (counted separately, never bucketed). Starting constants - the NLP
# review may retune them once the extractor actually emits scores.
CONFIDENCE_HIGH = 0.8
CONFIDENCE_MEDIUM = 0.5


class RecordCurrencyRollup(BaseModel):
    """A per-currency money rollup for one document's structured records. Money is NEVER summed
    across currencies (mirrors AggregationBucket). Records with a NULL direction count toward
    ``count`` but neither debit nor credit total."""

    currency: str | None = None
    debit_minor: int = 0  # SUM(amount_minor) WHERE direction='debit' (spend)
    credit_minor: int = 0  # SUM(amount_minor) WHERE direction='credit' (refund/payment)
    count: int = 0


class MerchantRollup(BaseModel):
    """A top merchant for one document, ranked by occurrence count (as specced). ``total_minor`` is
    a per-currency hint (the same merchant under two currencies appears as two rollups)."""

    merchant: str  # merchant_normalized (display)
    count: int = 0
    total_minor: int = 0
    currency: str | None = None


class RecordTypeCount(BaseModel):
    """How many records of a given record_type the document carries (record_type may diversify
    beyond 'card_transaction')."""

    record_type: str
    count: int = 0


class ConfidenceBuckets(BaseModel):
    """Extraction-confidence distribution for a document's records. Only rows with a non-NULL
    confidence are bucketed; NULL (never scored) rows are counted as ``unscored``. Today nothing
    scores, so summaries are honestly almost entirely ``unscored`` (see ExtractedRecord.confidence).
    """

    high: int = 0  # confidence >= CONFIDENCE_HIGH
    medium: int = 0  # CONFIDENCE_MEDIUM <= confidence < CONFIDENCE_HIGH
    low: int = 0  # confidence < CONFIDENCE_MEDIUM
    unscored: int = 0  # confidence IS NULL (never scored by a model)


class DocumentRecordSummary(BaseModel):
    """Compact structured-records rollup for the document detail card; the full row list is fetched
    on demand via GET /documents/{id}/records. Mirrors DocumentEntitySummary (summary eager, full
    list lazy). All rollups are per-currency - money is never summed across currencies."""

    total: int = 0
    by_currency: list[RecordCurrencyRollup] = Field(default_factory=list)
    by_type: list[RecordTypeCount] = Field(default_factory=list)
    date_from: date | None = None  # MIN(occurred_on)
    date_to: date | None = None  # MAX(occurred_on)
    top_merchants: list[MerchantRollup] = Field(default_factory=list)  # top ~5 by count
    confidence: ConfidenceBuckets = Field(default_factory=ConfidenceBuckets)
    # == confidence.low; surfaced flat for the trust strip (rows scored below CONFIDENCE_MEDIUM).
    low_confidence_count: int = 0


class DocumentRecordPage(BaseModel):
    """A page of a document's structured records (the lazy Transactions tab). Offset-paginated,
    ordered (occurred_on NULLS LAST, id). ``next_offset`` is None on the last page."""

    items: list[ExtractedRecord] = Field(default_factory=list)
    total: int = 0
    next_offset: int | None = None


class DocumentDetail(BaseModel):
    """One-round-trip aggregate for the document detail view's eager fold (review follow-up).

    Bundles everything shown immediately (identity, summary, processing, categories, an entity
    summary, a content excerpt, recent activity, and a structured-records rollup) so the card needs
    one request. The unbounded payloads - the full extracted text, the full entity list, and the
    full records list - stay behind their lazy endpoints and are only fetched when a tab is opened.
    """

    document: Document
    processing: ProcessingTelemetry = Field(default_factory=ProcessingTelemetry)
    features: list[DocumentFeature] = Field(default_factory=list)
    categories: list[Category] = Field(default_factory=list)
    entities: DocumentEntitySummary = Field(default_factory=DocumentEntitySummary)
    content: DocumentContentMeta = Field(default_factory=DocumentContentMeta)
    recent_activity: list[AuditEvent] = Field(default_factory=list)
    # Structured-records rollup (additive; default-empty for record-less documents - today's look).
    records: DocumentRecordSummary = Field(default_factory=DocumentRecordSummary)


class AiPurposeSettings(BaseModel):
    """Model choice for one AI purpose (the data pipeline, or document interrogation)."""

    provider: str = "ollama"  # 'ollama' | 'openai'
    model: str
    num_ctx: int
    reasoning: str = "off"  # 'off' | 'low' | 'medium' | 'high' (ignored by non-reasoning models)
    # Per-purpose Ollama server URL override (M13 #369). None = inherit DOKTOK_OLLAMA_BASE_URL.
    # Only used when provider == "ollama"; lets a purpose target a different Ollama host.
    ollama_base_url: str | None = None


class AiEmbeddingSettings(BaseModel):
    """Embedding configuration (M13 #369). The model is fixed (changing it needs a re-index), but
    the Ollama server URL can be overridden so embeddings run on a different host. None = inherit
    DOKTOK_OLLAMA_BASE_URL."""

    ollama_base_url: str | None = None


def _default_pipeline() -> AiPurposeSettings:
    return AiPurposeSettings(provider="ollama", model="qwen3.6:35b-a3b", num_ctx=8192)


def _default_rag() -> AiPurposeSettings:
    return AiPurposeSettings(provider="ollama", model="qwen3.6:35b-a3b", num_ctx=32768)


class AiSettings(BaseModel):
    """The configurable AI model selection (Settings tab > AI section). Applied on restart."""

    pipeline: AiPurposeSettings = Field(default_factory=_default_pipeline)
    rag: AiPurposeSettings = Field(default_factory=_default_rag)
    embedding: AiEmbeddingSettings = Field(default_factory=AiEmbeddingSettings)


class OcrSettings(BaseModel):
    """OCR processing settings (Settings tab > OCR, M7.6 / M17 #375).

    ``ocr_concurrency`` is the number of OCR pages processed in parallel and sizes the OCR
    worker-process pool. The worker live-reloads it (no restart) between ingest scans.
    ``engine`` selects the OCR engine ("paddleocr" | "rapidocr" | "glm-ocr"); empty inherits the
    DOKTOK_OCR_ENGINE default. An engine change applies on the next worker restart (M17).
    """

    ocr_concurrency: int = Field(default=4, ge=1, le=32)
    engine: str = ""


# Selectable OCR engines surfaced in the Settings UI (M17 #375).
OCR_ENGINES: tuple[str, ...] = ("paddleocr", "rapidocr", "glm-ocr")


class EgressBlockReason(StrEnum):
    """Why an AI purpose can't run as configured. Policy blocks (egress refused) are distinct from
    the usability gap (OpenAI selected but no key) - different cause, different remediation."""

    OPENAI_SELECTED = "openai_selected"  # provider=openai while DOKTOK_NO_EGRESS is on
    REMOTE_OLLAMA_URL = "remote_ollama_url"  # non-loopback Ollama URL while DOKTOK_NO_EGRESS is on
    OPENAI_KEY_MISSING = (
        "openai_key_missing"  # not a policy block: valid config, needs a key to run
    )


class PurposeEgressStatus(BaseModel):
    """Per-purpose (pipeline/rag/embedding) runtime status for the AI settings UI."""

    requires_egress: bool = False  # would this purpose move document content off-host?
    usable: bool = True  # can it actually run as configured right now?
    blocked_reason: EgressBlockReason | None = None  # machine-readable; None when usable


class AiSettingsResponse(AiSettings):
    """AI settings as returned to the UI - never the OpenAI key, only whether one is set."""

    openai_api_key_set: bool = False
    # Read-only: the embedding model + its context, shown so the user can see what indexes their
    # corpus. Not user-selectable - changing it would need a vector-dimension migration + re-index.
    embedding_model: str = ""
    embedding_num_ctx: int = 0
    # The effective default Ollama URL (DOKTOK_OLLAMA_BASE_URL) so the UI can show it as the
    # placeholder and "reset to default" target for each per-purpose override (M13 #369).
    ollama_base_url_default: str = ""
    # The active (effective) no-egress posture - the in-app toggle, or the env default, or forced on
    # by a host lock. The UI reflects it to gate/badge purposes that would leave the host.
    no_egress: bool = True
    # True when an operator hard-locked no-egress on the host (DOKTOK_NO_EGRESS_LOCK): it is forced
    # on and the in-app toggle is disabled.
    no_egress_locked: bool = False
    # Per-purpose runtime egress status, keyed "pipeline"|"rag"|"embedding": lets the UI show, per
    # row, "blocked by no-egress" vs "needs an API key" vs "running off-host", without redoing
    # loopback detection in the client.
    purpose_status: dict[str, PurposeEgressStatus] = Field(default_factory=dict)
    # True when any purpose actually moves content off-host right now (requires egress AND usable).
    # Drives a non-dismissable privacy indicator in the UI (APP-11). Covers OpenAI AND a remote
    # (non-loopback) Ollama URL, not just OpenAI.
    egress_active: bool = False


class AiSettingsUpdate(AiSettings):
    """AI settings update from the UI. ``openai_api_key`` is write-only: None leaves it unchanged,
    "" clears it, a value sets it. ``no_egress`` is the in-app toggle: None leaves it unchanged; a
    bool sets the posture (rejected when the host has hard-locked it)."""

    openai_api_key: str | None = None
    no_egress: bool | None = None


class OllamaTestRequest(BaseModel):
    """Probe an Ollama server before saving (M13 #369). ``url`` None/"" tests the default.
    ``model`` (optional) is also checked for being installed on that server (no model load)."""

    url: str | None = None
    model: str = ""


class OllamaTestResult(BaseModel):
    """Result of an Ollama reachability probe: whether it answered + a short human detail. When a
    ``model`` was supplied, ``model_present`` says whether it is installed (None = not checked)."""

    ok: bool
    detail: str
    url: str  # the effective URL that was probed (the override, or the default when blank)
    model: str = ""  # the model name that was checked, if any
    model_present: bool | None = (
        None  # installed? None when no model was checked / server unreachable
    )


class OllamaWarmupRequest(BaseModel):
    """Preload a model into an Ollama server (M13 #369 follow-up). Unlike Test (a fast reachability
    check), this triggers an actual model load so the first real request is not cold."""

    url: str | None = None
    model: str


class OllamaWarmupResult(BaseModel):
    """Result of a warm-up: whether the model loaded + a short human detail (no content)."""

    ok: bool
    detail: str
    url: str
    model: str


class OpenAiTestRequest(BaseModel):
    """Validate an OpenAI API key before saving (M13). ``api_key`` None/"" tests the stored key."""

    api_key: str | None = None


class OpenAiTestResult(BaseModel):
    """Result of an OpenAI key validation: whether it works + a short detail (no key echoed)."""

    ok: bool
    detail: str


class OllamaStatus(BaseModel):
    """Whether the in-stack Ollama container is needed (M16 #374). A host timer stops/starts the
    container to match, reclaiming its memory when every Ollama consumer is offloaded."""

    local_ollama_needed: bool
    embedding_url: str  # the effective embedding endpoint (per-purpose override or the default)


class OcrRecommendation(BaseModel):
    """Device-aware OCR suggestion (M17 #375): the engine + parallelism that best fit the detected
    host, with a short rationale shown as a hint in Settings."""

    engine: str  # paddleocr | rapidocr | glm-ocr
    concurrency: int
    reason: str


class IngestUploadResult(BaseModel):
    """Result of a UI document upload (M14 #370): files written into the tenant's ingest folder for
    the worker to pick up. ``rejected`` entries are "name: reason" strings."""

    accepted: list[str] = []
    rejected: list[str] = []


class BackupLegStatus(BaseModel):
    """Freshness of one backup leg, derived from the host-written sentinel (DRP, M12 #368).
    ``state`` is 'unknown' when the sentinel is missing/never-run - the UI shows that neutrally."""

    state: str = "unknown"  # ok | stale | failed | unknown
    last_run_at: datetime | None = None
    age_seconds: int | None = None
    detail: str = ""  # short human note (backup type, snapshot id) - never a secret
    # Backup metrics captured into the sentinel (M12 #380); all optional/best-effort.
    size: str = ""  # human-readable backup/snapshot size, e.g. "662 MiB"
    file_count: int | None = None
    backup_id: str = ""  # restic snapshot id / pgBackRest backup label


class DrpStatus(BaseModel):
    """Live freshness of each backup leg + the last restore drill (read from the sentinel files)."""

    files: BackupLegStatus = Field(default_factory=BackupLegStatus)
    pg: BackupLegStatus = Field(default_factory=BackupLegStatus)
    offsite: BackupLegStatus = Field(default_factory=BackupLegStatus)
    drill: BackupLegStatus = Field(default_factory=BackupLegStatus)
    wal_lag_seconds: int | None = None
    status_source_available: bool = False


class DrpConfig(BaseModel):
    """Static DR config (host/account owned; read-only in the UI). Secrets are presence-only."""

    rpo_files_seconds: int = 900
    rpo_pg_seconds: int = 60
    rpo_offsite_seconds: int = 3600
    rto_seconds: int = 14400
    deploy_mode: str = "host"  # host | compose - the topology backups run in (M12 #377)
    repo_location: str = ""
    azure_container: str = ""
    immutability_enabled: bool = False
    encryption_keys_configured: bool = False  # restic + pgBackRest cipher pass present (bools only)
    azure_credentials_configured: bool = False


class DrpStatusResponse(BaseModel):
    """The DRP (Disaster Recovery Plan) Settings panel payload: live freshness + static config.
    Entirely read-only - nothing here is editable via the API (#368)."""

    status: DrpStatus = Field(default_factory=DrpStatus)
    config: DrpConfig = Field(default_factory=DrpConfig)
    read_only: bool = True


class BackupEvent(BaseModel):
    """One entry from the append-only backup history (M12 DRP hardening).

    The authoritative record lives OUTSIDE Postgres in a host-written ``history.jsonl`` (so a DB
    restore can't roll backup history back). This is the on-the-wire projection of one line; the
    tamper-evidence fields (``prev_sha256``/``schema``) are deliberately NOT exposed - only ``seq``
    is surfaced so a consumer can see ordering. Never carries a secret, filename, or doc content.
    """

    ts: datetime
    leg: str  # files | pg | offsite | drill | prune
    event: str  # start | success | failure | prune | drill_pass | drill_fail
    ok: bool = False
    size: str = ""  # human-readable size, e.g. "662 MiB"
    item_count: int | None = None  # files snapshotted / rows verified in a drill
    backup_id: str = ""  # restic snapshot id / pgBackRest label
    duration_ms: int | None = None
    detail: str = ""  # short human note (truncated host-side); never a secret
    seq: int | None = None  # monotonic sequence number from the history chain


class DrpHistoryResponse(BaseModel):
    """A read-only window over the append-only backup history (M12 DRP hardening), newest-first.

    ``integrity_ok`` is False when the ``prev_sha256`` hash chain is broken across the read window,
    which surfaces tampering or truncation of the authoritative history file to the operator.
    """

    events: list[BackupEvent] = Field(default_factory=list)
    source_available: bool = False
    total_returned: int = 0
    truncated: bool = False
    integrity_ok: bool = True


class DrillTriggerResponse(BaseModel):
    """Result of requesting an on-demand restore drill (M12 DRP hardening). The backend only drops a
    request file the host watches; it never executes the drill itself."""

    accepted: bool
    detail: str = ""
    last_drill_at: datetime | None = None


class BackupManifestMember(BaseModel):
    """One member of a portable backup archive, with its size + checksum (M12 portable backup,
    Phase 1). ``name`` is the in-archive path (``db.dump`` or a ``files/...`` entry summary)."""

    name: str
    size: int  # bytes
    sha256: str  # hex digest of the member's bytes


class BackupManifest(BaseModel):
    """The ``manifest.json`` embedded in a portable backup archive (M12 portable backup, Phase 1).

    Internal/at-rest model (not returned verbatim on the wire). Carries the per-member checksums, a
    manifest-level integrity tag over those checksums (verified by a later restore), and a
    NON-REVERSIBLE fingerprint of DOKTOK_SECRETS_KEY so a restore can warn on a key mismatch WITHOUT
    ever storing the key. The archive is DATA-ONLY: it never contains the secrets key, tenant
    tokens, the database URL, or .env. The OpenAI key inside app_settings stays as its existing
    Fernet ciphertext (only decryptable on a host configured with the same DOKTOK_SECRETS_KEY).
    """

    schema_version: int = 1
    created_at: datetime
    app_version: str
    pg_version: str
    # The DB schema/migration generation the archive was produced at (the latest applied migration
    # number, M12 portable restore Phase 2). A restore refuses an archive NEWER than the running
    # code (restoring a newer dump into older code is unsafe); older-or-equal is migrated forward.
    # 0 means "unknown" (a Phase-1 archive predating this field) -> treated as compatible.
    app_schema_version: int = 0
    members: list[BackupManifestMember] = Field(default_factory=list)
    # HMAC-SHA256 (hex) over the sorted "name:sha256" member lines; integrity check on restore.
    manifest_hmac: str = ""
    # Non-reversible fingerprint of DOKTOK_SECRETS_KEY (HMAC of a fixed label). Empty when no key is
    # configured (plaintext-secrets dev mode). A restore compares this to warn on a key mismatch.
    secrets_key_fingerprint: str = ""


class BackupExportInfo(BaseModel):
    """Status of a portable backup export build (M12 portable backup, Phase 1).

    The build runs ASYNCHRONOUSLY: POST /export returns this with status='building'; the client
    polls GET /export/status until status='ready' (then it may download) or 'failed'. The wire model
    never carries the staged file path, the passphrase, or any secret - only a summary."""

    export_id: str
    status: str  # building | ready | failed
    created_at: datetime | None = None
    size_bytes: int | None = None  # size of the PLAINTEXT staged archive (pre-encryption)
    app_version: str = ""
    pg_version: str = ""
    member_count: int = 0
    error: str = ""  # short, non-secret failure summary when status='failed'


class RestorePreview(BaseModel):
    """Result of POST /backup/restore/preview (M12 portable restore Phase 2): the NON-destructive
    validation verdict for an uploaded encrypted archive.

    The preview streams the upload to disk, decrypts it (passphrase on stdin only), safely extracts
    it to a staging dir, recomputes every member checksum + the manifest HMAC, and checks version
    compatibility. ``ok`` is True only when the archive is intact AND compatible; only then may the
    apply step proceed against this ``staged_id`` (retained briefly under a TTL). NO secret, no
    passphrase, no DSN, and no host path is ever returned."""

    staged_id: str  # opaque id of the validated staging dir; passed to /apply
    ok: bool  # True only when there are no hard errors (intact + compatible)
    compatible: bool  # version-compatibility verdict (pg major + app schema generation)
    app_version: str = ""  # the app version stamped in the archive's manifest
    pg_version: str = ""  # the Postgres server version stamped in the archive's manifest
    created_at: datetime | None = None  # when the archive was produced
    member_count: int = 0  # number of archive members (db.dump + each files/ entry)
    total_bytes: int = 0  # total uncompressed size across members
    secrets_key_match: bool = False  # archive's secrets_key_fingerprint == this box's
    warnings: list[str] = Field(default_factory=list)  # non-fatal (e.g. secrets-key mismatch)
    errors: list[str] = Field(default_factory=list)  # hard failures; apply is refused when present


class RestoreApplyRequest(BaseModel):
    """Body of POST /backup/restore/{staged_id}/apply. ``confirm`` MUST be true - a destructive,
    whole-system restore is never triggered implicitly (422 otherwise; confirm-to-destroy)."""

    confirm: bool = False


class RestoreResult(BaseModel):
    """Result of POST /backup/restore/{staged_id}/apply. The apply runs ASYNCHRONOUSLY out-of-band
    (a root host helper does the destruction; the backend only drops a request file), so this just
    acknowledges acceptance. The client then polls GET /backup/restore/status."""

    accepted: bool  # True once the restore request has been queued for the host helper
    restore_id: str = ""  # opaque id correlating the request, status, and history events
    detail: str = ""  # short, non-secret human message


class RestoreStatus(BaseModel):
    """Result of GET /backup/restore/status (M12 portable restore Phase 2): the current restore
    state, sourced from a sentinel OUTSIDE Postgres (the DB is rewritten mid-restore, so a DB-backed
    status would be unreadable/rolled-back). Never carries a secret/path/DSN."""

    state: str = "idle"  # idle | validating | applying | done | failed
    step: str = ""  # short label of the current/last phase (e.g. "snapshot", "db", "files")
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail: str = ""  # short, non-secret human message (e.g. a failure summary)
    restore_id: str = ""  # opaque id correlating the request and history events


class ModelOption(BaseModel):
    """A selectable model for a purpose, with its allowed context sizes."""

    provider: str
    model: str
    label: str
    contexts: list[int]
    supports_reasoning: bool = False
    # True for any provider that sends content off-host (OpenAI). The UI disables/badges these when
    # no-egress is on. A local Ollama option is False; a remote Ollama *URL* is gated separately
    # (per-purpose, via PurposeEgressStatus) since the option itself is provider-agnostic.
    requires_egress: bool = False


class ModelCatalog(BaseModel):
    """The models the Settings UI offers per purpose + the reasoning-density levels."""

    pipeline: list[ModelOption] = Field(default_factory=list)
    rag: list[ModelOption] = Field(default_factory=list)
    reasoning_levels: list[str] = Field(default_factory=list)
    # The active no-egress policy, so the catalog alone tells the UI which options to disable/badge.
    no_egress: bool = True


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
