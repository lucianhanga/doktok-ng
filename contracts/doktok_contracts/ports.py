"""Core ports (interfaces) for DokTok NG.

Core domain logic depends on these Protocols, never on concrete adapters (ADR-0001). Adapters in
providers/, storage/, modalities/, and retrieval/ implement them. For M0 these are interface
declarations only; methods are intentionally unimplemented.

See brief section 9 for the full list.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from doktok_contracts.media import (
    ChatChunk,
    ExtractedEntity,
    ExtractedMetadata,
    ExtractedRelation,
    ExtractedTerm,
    ExtractedTransaction,
    LlmUsage,
    OcrPageResult,
    ProjectionResult,
    RenderedPage,
    TextChunk,
)
from doktok_contracts.schemas import (
    AggregationIntent,
    AggregationResult,
    AiSettings,
    AuditEvent,
    Category,
    CategorySummary,
    ChatEvent,
    ChatMessage,
    ChatThread,
    ChatTurn,
    Citation,
    Document,
    DocumentArtifact,
    DocumentChunk,
    DocumentEntity,
    DocumentFeature,
    DocumentRecordSummary,
    DocumentSort,
    DocumentStatus,
    DocumentVersion,
    EmbeddingProjection,
    EntitySummary,
    EntityType,
    ExtractedRecord,
    FeatureMetrics,
    IngestionJob,
    KgEdge,
    KgEdgeProvenance,
    KgEntity,
    KgEntityMention,
    ListAnchor,
    OcrSettings,
    ProjectionRequest,
    QueryFilters,
    RagAnswer,
    RankedChunk,
    SearchHit,
    SecurityDecision,
    SortDir,
    StatsSummary,
    TokenMatch,
    TokenSuggestion,
    TurnMetrics,
)

# --- Repositories ---------------------------------------------------------------------------


@runtime_checkable
class DocumentRepository(Protocol):
    def get(self, tenant_id: str, document_id: str) -> Document | None: ...
    def add(self, document: Document) -> None:
        """Insert a document. Raises ``DuplicateActiveDocumentError`` if an active document with the
        same (tenant_id, sha256) already exists (the content-dedup invariant)."""
        ...

    def find_active_by_sha256(self, tenant_id: str, sha256: str) -> str | None:
        """Id of an active document with this content hash, if any. Authoritative content-dedup
        source - matches the uq_documents_active_sha invariant (not the job ledger)."""
        ...

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
        """Keyset-paginated documents ordered by ``sort``/``direction`` with ``id`` as tie-breaker.

        ``cursor`` is the ``ListAnchor`` of the last row already seen (None = first page); it must
        match the requested ``sort``/``direction``. Null sort values always sort last. ``title``
        keeps only documents whose title contains it (case-insensitive substring). ``tokens``
        keeps only documents carrying those entity/keyword values (combined per ``token_match``,
        optionally constrained to ``token_type``); ``needs_attention`` keeps documents with a
        non-done feature; ``category`` keeps documents linked to that active category. All filters
        compose. Returns ``(items, total, next_anchor)``; ``next_anchor`` is None on the last page.
        """
        ...

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
        """All document ids matching the filters (same filters as ``list_documents``, no paging),
        for 'select all matching' bulk actions. Returns ``(ids, total, truncated)``; when more than
        ``cap`` match, ``ids`` holds the first ``cap`` (by id) and ``truncated`` is True.
        """
        ...

    def delete(self, tenant_id: str, document_id: str) -> None: ...
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
        """Persist enrichment fields (M6.2). Idempotent overwrite."""
        ...

    def set_unidentifiable(self, tenant_id: str, document_id: str, *, value: bool | None) -> None:
        """Set the unidentifiable marker (M7.3, ADR-0017). True/False = assessed; None = unassessed.
        Idempotent overwrite, separate from set_metadata so detection can write it independently."""
        ...

    def activate(
        self,
        tenant_id: str,
        document_id: str,
        *,
        storage_path: str,
        metadata: dict[str, object],
    ) -> bool:
        """Flip a ``processing`` document to ``active`` once its content is extracted (ADR-0015):
        sets the storage path + metadata and stamps ``activated_at``/``ingested_at``. Returns False
        if the document is not ``processing`` (already activated or gone). Raises
        ``DuplicateActiveDocumentError`` if another active document already holds this content (the
        content-dedup race, ``uq_documents_active_sha``)."""
        ...


@runtime_checkable
class DocumentVersionRepository(Protocol):
    def get(self, version_id: str) -> DocumentVersion | None: ...
    def add(self, version: DocumentVersion) -> None: ...


@runtime_checkable
class IngestionJobRepository(Protocol):
    def get(self, tenant_id: str, job_id: str) -> IngestionJob | None: ...
    def add(self, job: IngestionJob) -> None: ...
    def update(self, job: IngestionJob) -> None: ...
    def list_jobs(self, tenant_id: str, limit: int = 50, offset: int = 0) -> list[IngestionJob]: ...
    def find_by_sha256(self, tenant_id: str, sha256: str) -> list[IngestionJob]: ...
    def delete_for_document(self, tenant_id: str, document_id: str) -> None:
        """Delete the ingestion jobs that belong to this document, when it is deleted or
        reingested. Scoped by ``document_id`` (not content hash) so purging one document never
        removes the job of another document that happens to share the same content."""
        ...

    def list_in_flight(self, tenant_id: str, *, before: datetime) -> list[IngestionJob]:
        """Jobs stuck in a non-terminal state (created before ``before``) - abandoned mid-pipeline
        when a worker died. Used to re-queue them so they don't linger invisibly forever."""
        ...

    def delete(self, tenant_id: str, job_id: str) -> None:
        """Delete a single job by id (tenant-scoped)."""
        ...


@runtime_checkable
class DocumentArtifactRepository(Protocol):
    def add(self, artifact: DocumentArtifact) -> None: ...
    def list_for_document(self, document_id: str) -> list[DocumentArtifact]: ...


@runtime_checkable
class AppSettingsRepository(Protocol):
    """Persisted global system settings (the Settings tab). Not tenant-scoped - single-user config.

    The OpenAI key is stored separately and write-only: it is never returned, only set/cleared.
    """

    def get_ai_settings(self) -> AiSettings: ...
    def set_ai_settings(self, settings: AiSettings) -> None: ...
    def has_ai_settings(self) -> bool:
        """True if AI settings have been explicitly saved (vs the unset defaults). Lets headless
        bootstrap seed a fresh deployment without overwriting an operator's later edits (APP-2)."""
        ...

    def get_openai_api_key(self) -> str: ...
    def set_openai_api_key(self, key: str) -> None: ...
    def get_no_egress(self) -> bool | None:
        """The in-app no-egress override (the Settings > AI toggle), or None if never set - in which
        case the env default applies. A host lock (DOKTOK_NO_EGRESS_LOCK) overrides both."""
        ...

    def set_no_egress(self, value: bool) -> None: ...
    def get_ocr_settings(self) -> OcrSettings: ...
    def set_ocr_settings(self, settings: OcrSettings) -> None: ...
    def set_worker_heartbeat(self) -> None:
        """Stamp the worker liveness timestamp (APP-5). Called periodically by the worker so an
        external probe (the backend /ready) can detect a dead or stuck worker."""
        ...

    def get_worker_heartbeat(self) -> datetime | None:
        """The last worker heartbeat time, or None if the worker has never beaten."""
        ...

    def set_maintenance_mode(self, *, enabled: bool) -> None:
        """Toggle quiesce/maintenance mode (APP-C3). While on, the worker starts no new ingestion or
        reconcile work, letting a backup capture a still DB + files_root pair."""
        ...

    def get_maintenance_mode(self) -> bool:
        """Whether quiesce/maintenance mode is on. The worker reads this each loop."""
        ...

    def get_backup_status(self) -> dict[str, dict[str, object]] | None:
        """Per-leg backup freshness from the host-written sentinels (DRP, #368), keyed by leg
        (files/pg/offsite/drill). Read-only and sourced OUTSIDE the database (a file on the shared
        backup volume) so a Postgres restore can't roll backup status back. None if unavailable."""
        ...

    def get_backup_history(
        self, limit: int = 100, leg: str | None = None
    ) -> tuple[list[dict[str, object]], bool, bool, bool]:
        """A bounded, newest-first window over the host-written append-only backup history
        (``history.jsonl``, M12 DRP hardening). Like ``get_backup_status`` this is sourced OUTSIDE
        Postgres so a DB restore can't roll history back. Optionally filtered to one ``leg``.

        Returns ``(events, source_available, truncated, integrity_ok)`` where ``events`` is a list
        of raw dicts (newest-first, capped at ``limit``), ``source_available`` is False when the
        history file is missing/empty, ``truncated`` is True when older entries were dropped to stay
        within the read cap, and ``integrity_ok`` is False when the ``prev_sha256`` hash chain is
        broken across the read window (tamper signal). Never raises on a missing/corrupt file."""
        ...


@runtime_checkable
class AuditLogRepository(Protocol):
    """Append-only activity/audit trail (record + read only; events are immutable)."""

    def record(self, event: AuditEvent) -> None: ...
    def list_events(
        self,
        tenant_id: str,
        *,
        document_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]: ...


# --- File / IO ------------------------------------------------------------------------------


@runtime_checkable
class FileStorage(Protocol):
    def move(self, source: str, destination: str) -> None: ...
    def read_bytes(self, path: str) -> bytes: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    def write_text(self, path: str, text: str) -> None: ...


@runtime_checkable
class MimeDetector(Protocol):
    def detect(self, path: str) -> str: ...


@runtime_checkable
class HashService(Protocol):
    def sha256(self, path: str) -> str: ...


# --- Extraction -----------------------------------------------------------------------------


@runtime_checkable
class TextExtractor(Protocol):
    def extract(self, path: str) -> str: ...


@runtime_checkable
class DocumentNormalizer(Protocol):
    """Convert an office/other document (docx/xlsx/pptx/...) to PDF bytes (M8.x #313), so it can
    reuse the canonical PDF extraction/render/OCR/preview path. Runs locally (e.g. a Gotenberg/
    LibreOffice container) - document content never leaves the host."""

    def to_pdf(self, path: str, mime: str) -> bytes: ...


@runtime_checkable
class PdfClassifier(Protocol):
    def page_image_coverage(self, path: str) -> list[float]:
        """Per page, the fraction (0-1) of the page area covered by its largest embedded image."""
        ...


@runtime_checkable
class PdfTextExtractor(Protocol):
    def extract_pages(self, path: str) -> list[str]: ...


@runtime_checkable
class OcrExtractor(Protocol):
    def ocr_image(self, image_png: bytes) -> OcrPageResult: ...


@runtime_checkable
class PdfRenderer(Protocol):
    def render_pages(self, path: str, dpi: int = 200) -> list[bytes]: ...


@runtime_checkable
class SearchablePdfBuilder(Protocol):
    def build(self, pages: list[RenderedPage]) -> bytes: ...


@runtime_checkable
class Thumbnailer(Protocol):
    def thumbnail(self, source_path: str, *, max_edge: int = 400) -> bytes:
        """Render a small preview image (WebP bytes) of the document's first page, with the long
        edge scaled to ``max_edge`` pixels and the page aspect ratio preserved."""
        ...


@runtime_checkable
class ImageExtractor(Protocol):
    def extract(self, path: str) -> str: ...


@runtime_checkable
class MarkdownExtractor(Protocol):
    def extract(self, path: str) -> str: ...


# --- Indexing / AI --------------------------------------------------------------------------


@runtime_checkable
class Chunker(Protocol):
    def chunk(self, text: str) -> list[TextChunk]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class ChunkRepository(Protocol):
    def add_chunks(self, chunks: list[DocumentChunk], embeddings: list[list[float]]) -> None: ...
    def delete_for_document(self, tenant_id: str, document_id: str) -> None: ...

    def read_embeddings(self, tenant_id: str, limit: int) -> list[tuple[str, str, list[float]]]:
        """Read up to ``limit`` (chunk_id, document_id, embedding) rows for the embed map (M7.1).

        Ordered deterministically (by chunk id) so a truncated read is stable across calls.
        """
        ...

    def embedding_fingerprint(self, tenant_id: str) -> str:
        """A cheap fingerprint of the tenant's embeddings (count + newest row) for staleness (M7.1).

        Lets the Insights tab tell whether a cached projection is stale without re-reading vectors.
        """
        ...

    def read_texts(self, tenant_id: str, chunk_ids: list[str]) -> dict[str, str]:
        """Map each requested chunk id to its text, for embedding-map tooltips (M7.1)."""
        ...


@runtime_checkable
class EmbeddingProjector(Protocol):
    """Fit the embedding map: PCA pre-reduce -> UMAP per dim + HDBSCAN clustering (M7.1/M7.2)."""

    def project(self, vectors: list[list[float]], dims: Sequence[int]) -> ProjectionResult:
        """Return per-dimension coordinates + one cluster id per vector (same input order).

        The cluster id is shared across dimensions, so 2D and 3D color the same chunk identically.
        """
        ...

    def prewarm(self) -> None:
        """Trigger UMAP/HDBSCAN JIT compilation up front so the first real fit is not slow."""
        ...


@runtime_checkable
class ProjectionRequestRepository(Protocol):
    """The DB-backed recompute queue for embedding projections (ADR-0016, M7.1)."""

    def request(self, tenant_id: str) -> None:
        """Enqueue a recompute for a tenant (idempotent while one is already pending)."""
        ...

    def has_pending(self, tenant_id: str) -> bool:
        """Whether a recompute is queued/running for the tenant (drives the UI busy state)."""
        ...

    def claim_next(self) -> ProjectionRequest | None:
        """Claim the oldest pending request across tenants (marks it running); None if none."""
        ...

    def complete(self, request_id: str) -> None:
        """Remove a finished request from the queue."""
        ...


@runtime_checkable
class EmbeddingProjectionRepository(Protocol):
    """Cache of computed 2D/3D embedding-space projections for the Insights tab (ADR-0016, M7.1)."""

    def upsert(self, projection: EmbeddingProjection) -> None:
        """Replace the cached projection for (tenant_id, dim) with this one (points included)."""
        ...

    def get(self, tenant_id: str, dim: int) -> EmbeddingProjection | None:
        """Latest cached projection for (tenant, dim), points included; None if not computed."""
        ...

    def get_header(self, tenant_id: str, dim: int) -> EmbeddingProjection | None:
        """Projection metadata only (points empty) for status checks; None if not computed."""
        ...


@runtime_checkable
class ChatModelProvider(Protocol):
    def complete(self, prompt: str) -> str: ...


@runtime_checkable
class UsageReportingChatModel(Protocol):
    """Optional capability (M8): a chat provider that records token/timing for its most recent call.

    The answerer checks for it and collects usage after ``complete``/``stream_complete``; providers
    that don't implement it simply yield no metrics. ``get_last_usage`` reflects the last call made
    on this provider instance (None if it has not been called or usage was unavailable)."""

    def get_last_usage(self) -> LlmUsage | None: ...


@runtime_checkable
class StreamingChatModelProvider(Protocol):
    """A chat model that can stream its response (M6.4). Optional capability: the answerer checks
    for it and falls back to ``ChatModelProvider.complete`` when a provider doesn't support it."""

    def stream_complete(self, prompt: str, *, think: bool | None = None) -> Iterator[ChatChunk]:
        """Yield answer (and, if reasoning is on, reasoning) chunks as the model generates them.
        ``think=None`` uses the provider's configured reasoning (from settings); True/False
        overrides it for this call."""
        ...


@runtime_checkable
class MetadataExtractor(Protocol):
    """Extract enrichment fields (title/date/location/summary) from document text (M6.2).

    Returns raw model output; callers validate/normalize in core (e.g. ISO date or n/a).
    """

    def extract(self, text: str) -> ExtractedMetadata: ...


@runtime_checkable
class CategoryClassifier(Protocol):
    """Assign up to 5 category labels, preferring the supplied existing vocabulary (M6.2)."""

    def classify(self, text: str, existing: list[str]) -> list[str]: ...


@runtime_checkable
class RecordExtractor(Protocol):
    """Extract structured line items (transactions) from a financial document (M6.3).

    Returns raw rows; core validates/normalizes (money -> minor units, dates, merchant). Returns an
    empty list for non-financial documents.
    """

    def extract(self, text: str) -> list[ExtractedTransaction]: ...


@runtime_checkable
class RecordRepository(Protocol):
    """Structured extracted records + deterministic aggregation (M6.3)."""

    def replace_for_document(
        self, tenant_id: str, document_id: str, records: list[ExtractedRecord]
    ) -> None:
        """Idempotently replace a document's records (delete then insert)."""
        ...

    def list_for_document(self, tenant_id: str, document_id: str) -> list[ExtractedRecord]: ...

    def list_for_document_page(
        self, tenant_id: str, document_id: str, *, limit: int, offset: int
    ) -> tuple[list[ExtractedRecord], int]:
        """One offset page of a document's records (ordered occurred_on NULLS LAST, id) plus the
        total count, for the lazy GET /documents/{id}/records endpoint."""
        ...

    def record_summary(self, tenant_id: str, document_id: str) -> DocumentRecordSummary:
        """Compact per-document rollup for the detail card: per-currency debit/credit totals +
        count, date range, top merchants (by count), record-type counts, and confidence buckets
        (NULL confidence counted as unscored). Money is summed per currency, never across them."""
        ...

    def aggregate(self, tenant_id: str, intent: AggregationIntent) -> AggregationResult:
        """Deterministic typed aggregation (sum/count) over a tenant's records, filtered by the
        intent (merchant fuzzy-match, type, direction, currency, date range). Money is summed per
        currency, never across them."""
        ...


@runtime_checkable
class CategoryRepository(Protocol):
    """Controlled-vocabulary categories + the document<->category links (M6.2)."""

    def list_active(self, tenant_id: str) -> list[Category]: ...
    def find_by_normalized(self, tenant_id: str, normalized: str) -> Category | None: ...
    def find_similar(
        self, tenant_id: str, normalized: str, *, threshold: float = 0.55
    ) -> Category | None: ...
    def find_nearest(self, tenant_id: str, normalized: str) -> Category | None: ...
    def active_count(self, tenant_id: str) -> int: ...
    def create(self, tenant_id: str, name: str, normalized: str) -> Category | None:
        """Create a category, or return None if the tenant is at the 20-category cap (race-safe)."""
        ...

    def set_document_categories(
        self, tenant_id: str, document_id: str, category_ids: list[str]
    ) -> None: ...
    def list_for_document(self, tenant_id: str, document_id: str) -> list[Category]: ...
    def list_summary(self, tenant_id: str) -> list[CategorySummary]: ...
    def documents_for_category(
        self, tenant_id: str, name: str, *, limit: int = 50, offset: int = 0
    ) -> list[Document]: ...

    def primary_categories(self, tenant_id: str, document_ids: list[str]) -> dict[str, str]:
        """Map each document to its single primary category name, for embedding-map coloring (M7.1).

        The primary is the linked category with the highest tenant-wide document count (name as
        tiebreak). Documents with no category are omitted (the caller colors them 'Uncategorized').
        """
        ...


@runtime_checkable
class EntityExtractor(Protocol):
    def extract(self, text: str) -> list[ExtractedEntity]: ...


@runtime_checkable
class EntityNerExtractor(Protocol):
    """Named-entity recognition for PERSON / ORG / GPE (LLM-assisted, M7.4).

    Separate port from ``EntityExtractor`` so the rule-based and NER adapters compose independently;
    both return ``ExtractedEntity`` occurrences (NER emits only the PERSON/ORG/GPE types).
    """

    def extract(self, text: str) -> list[ExtractedEntity]: ...


@runtime_checkable
class RelationExtractor(Protocol):
    """Extract directed relation triples between named entities (KAG Phase 2).

    ``entity_list`` is a list of ``(normalized_name, entity_type)`` pairs grounding the extraction
    to entities already known to be in the document. The extractor MUST only return triples whose
    subject and object appear in that list (the circuit-breaker in core enforces this too, but
    well-behaved extractors can filter up front).
    """

    def extract(self, text: str, entity_list: list[tuple[str, str]]) -> list[ExtractedRelation]:
        """Extract relation triples from text, grounded to entity_list (name, type) pairs."""
        ...


@runtime_checkable
class LexicalTermExtractor(Protocol):
    """Extract significant lexemes (stopwords removed) from text, language-aware (M5.7)."""

    def extract_terms(
        self, text: str, *, config: str = "simple", limit: int = 200
    ) -> list[ExtractedTerm]: ...


@runtime_checkable
class EntityRepository(Protocol):
    def add_entities(self, entities: list[DocumentEntity]) -> None: ...
    def delete_for_document(self, tenant_id: str, document_id: str) -> None: ...
    def delete_for_document_types(
        self, tenant_id: str, document_id: str, entity_types: list[str]
    ) -> None: ...
    def list_for_document(self, tenant_id: str, document_id: str) -> list[DocumentEntity]: ...
    def list_distinct(
        self,
        tenant_id: str,
        *,
        entity_type: EntityType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EntitySummary]: ...
    def documents_for_entity(
        self,
        tenant_id: str,
        entity_type: EntityType,
        normalized_value: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]: ...
    def suggest_tokens(
        self,
        tenant_id: str,
        prefix: str,
        *,
        selected: list[str] | None = None,
        limit: int = 10,
    ) -> list[TokenSuggestion]: ...
    def documents_for_tokens(
        self,
        tenant_id: str,
        tokens: list[str],
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]: ...


@runtime_checkable
class KnowledgeGraphRepository(Protocol):
    """Canonical entity-node graph built from ``document_entities`` mentions (KAG Phase 1).

    Deterministic, tenant-scoped, idempotent. ``upsert_entities`` creates canonical nodes (existing
    nodes are left untouched - the node identity is a pure function of type+value).
    ``replace_mentions_for_document`` rebuilds one document's mention links in place, so the
    reconciler can re-run a document or backfill the corpus safely.
    """

    def upsert_entities(self, entities: list[KgEntity]) -> None:
        """Insert canonical nodes; nodes that already exist are left unchanged (idempotent)."""
        ...

    def replace_mentions_for_document(
        self, tenant_id: str, document_id: str, mentions: list[KgEntityMention]
    ) -> None:
        """Idempotently replace a document's mention links (delete then insert)."""
        ...

    def get_entity(self, tenant_id: str, entity_id: str) -> KgEntity | None: ...

    def mentions_for_document(self, tenant_id: str, document_id: str) -> list[KgEntityMention]: ...

    def mentions_for_entity(self, tenant_id: str, entity_id: str) -> list[KgEntityMention]:
        """All mentions (across documents) resolving to one canonical node - the cross-document
        readout and a Phase-3 traversal seed."""
        ...

    def entity_count(self, tenant_id: str) -> int:
        """Number of distinct canonical nodes for a tenant."""
        ...

    def replace_edges_for_document(
        self,
        tenant_id: str,
        document_id: str,
        edges: list[KgEdge],
        provenance: list[KgEdgeProvenance],
    ) -> None:
        """Idempotently replace all edges sourced from this document.

        Deletes all provenance rows for ``document_id``, upserts the new edge rows, inserts the
        new provenance rows, then recomputes ``evidence_count`` and prunes edges whose count drops
        to zero (no remaining provenance from any document).
        """
        ...

    def edges_for_entity(self, tenant_id: str, entity_id: str) -> list[KgEdge]:
        """All edges where ``entity_id`` is the source or destination (inbound + outbound)."""
        ...

    def edge_count(self, tenant_id: str) -> int:
        """Number of distinct directed edges for a tenant."""
        ...


@runtime_checkable
class Retriever(Protocol):
    def search(
        self,
        tenant_id: str,
        query: str,
        limit: int = 10,
        *,
        filters: QueryFilters | None = None,
    ) -> list[SearchHit]: ...


@runtime_checkable
class Reranker(Protocol):
    """Reorder retrieved hits by relevance to the query and return the best ``top_k`` (M6.1)."""

    def rerank(self, query: str, hits: list[SearchHit], *, top_k: int) -> list[SearchHit]: ...


@runtime_checkable
class RagAnswerer(Protocol):
    def answer(self, tenant_id: str, question: str, limit: int = 8) -> RagAnswer: ...

    def answer_thread(
        self, tenant_id: str, history: list[ChatTurn], question: str, limit: int = 8
    ) -> RagAnswer:
        """Answer a follow-up in a conversation (ADR-0018): rewrite (history + question) into a
        standalone retrieval query, then answer it grounded + cited. ``history`` feeds only the
        rewrite, never the answer prompt. Empty history == single-turn ``answer``."""
        ...

    def answer_thread_stream(
        self,
        tenant_id: str,
        history: list[ChatTurn],
        question: str,
        limit: int = 8,
        *,
        reasoning: bool | None = None,
    ) -> Iterator[ChatEvent]:
        """Streaming variant of ``answer_thread`` (M6.4): yields meta / reasoning / token / sources
        / done events. ``reasoning=None`` follows the configured model reasoning (settings);
        True/False overrides it for this turn."""
        ...


@runtime_checkable
class ChatThreadRepository(Protocol):
    """Server-side persistence of chat conversations (M6.4 #248). Tenant-scoped."""

    def create_thread(self, tenant_id: str, title: str = "") -> ChatThread: ...
    def list_threads(self, tenant_id: str, *, limit: int = 50) -> list[ChatThread]: ...
    def get_messages(self, tenant_id: str, thread_id: str) -> list[ChatMessage]: ...
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
    ) -> ChatMessage: ...
    def thread_exists(self, tenant_id: str, thread_id: str) -> bool: ...
    def delete_thread(self, tenant_id: str, thread_id: str) -> None: ...
    def delete_messages_from(self, tenant_id: str, thread_id: str, message_id: str) -> int:
        """Delete ``message_id`` and every message after it in the thread (chronological order).
        Used to truncate a conversation when a question is deleted or edited. Returns the count
        removed (0 if the message isn't in the caller's thread)."""
        ...

    def update_title(self, tenant_id: str, thread_id: str, title: str) -> ChatThread | None:
        """Rename a thread (sets title_source='manual'). Returns the updated thread, or None when it
        does not belong to the tenant."""
        ...


@runtime_checkable
class StatsRepository(Protocol):
    def summary(self, tenant_id: str) -> StatsSummary: ...


@runtime_checkable
class FeatureProcessor(Protocol):
    """A named, versioned, idempotent document-processing capability (ADR-0009)."""

    name: str
    version: int

    def process(self, tenant_id: str, document_id: str) -> None: ...


@runtime_checkable
class FeatureRepository(Protocol):
    """The per-document feature ledger; the unit of work for the reconciler (ADR-0009)."""

    def record_done(
        self, tenant_id: str, document_id: str, feature: str, feature_version: int
    ) -> None: ...
    def ensure_for_active(self, tenant_id: str, features: list[tuple[str, int]]) -> int: ...
    def seed_for_document(
        self, tenant_id: str, document_id: str, stages: list[tuple[str, int]]
    ) -> int:
        """Seed pending stage rows for one document before it is active (e.g. a ``processing``
        document at intake). Idempotent: skips stages that already have a row. Returns the count."""
        ...

    def claim_next(
        self,
        tenant_id: str,
        *,
        now: datetime,
        reclaim_before: datetime,
        dependencies: Sequence[tuple[str, str]] = (),
    ) -> DocumentFeature | None:
        """Claim the next due feature row (pending / retryable-failed / stale-running). A row is
        only claimable once every ``(feature, prerequisite)`` edge in ``dependencies`` for its
        feature has a ``done`` row on the same document - so a stage waits for its inputs."""
        ...

    def mark_done(
        self, feature_id: str, *, feature_version: int, metrics: FeatureMetrics | None = None
    ) -> None:
        """Mark a feature row done. When ``metrics`` is given (the reconciler measured this run),
        persist it onto the row's ``metrics`` jsonb; when None, leave the column unchanged."""
        ...

    def mark_failed(self, feature_id: str, *, error: str, next_attempt_at: datetime) -> None: ...
    def feature_counts_for_documents(
        self, tenant_id: str, document_ids: list[str]
    ) -> dict[str, tuple[int, int]]:
        """(done, failed) feature counts per document for the list tooltip, in one batched query.
        Documents with no feature rows are absent from the returned map."""
        ...

    def list_for_document(self, tenant_id: str, document_id: str) -> list[DocumentFeature]: ...
    def list_for_tenant(self, tenant_id: str, *, limit: int = 2000) -> list[DocumentFeature]: ...
    def list_for_documents(self, tenant_id: str, document_ids: list[str]) -> list[DocumentFeature]:
        """Feature rows for a specific set of documents (the list view's visible page), uncapped.

        The badge view must cover exactly the documents on screen; ``list_for_tenant``'s row cap can
        otherwise silently drop the newest documents' badges once a tenant has many documents.
        """
        ...

    def reset(self, tenant_id: str, document_id: str, feature: str) -> bool: ...
    def requeue_running(self, tenant_id: str) -> int:
        """Reset any ``running`` feature rows back to ``pending`` (returns the count). Called at
        worker startup: with no worker draining yet, a ``running`` row can only be an orphan left by
        a previously killed worker, so recover it immediately instead of waiting out the lease."""
        ...


# --- Security -------------------------------------------------------------------------------


@runtime_checkable
class SecurityPolicy(Protocol):
    def is_allowed(self, mime: str, size_bytes: int) -> bool: ...
    def decide(self, mime: str, size_bytes: int) -> SecurityDecision: ...


@runtime_checkable
class QuarantineService(Protocol):
    def quarantine(self, path: str, reason: str) -> None: ...
