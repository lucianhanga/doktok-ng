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
    ExtractedTerm,
    ExtractedTransaction,
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
    ChatTurn,
    Document,
    DocumentArtifact,
    DocumentChunk,
    DocumentEntity,
    DocumentFeature,
    DocumentSort,
    DocumentStatus,
    DocumentVersion,
    EmbeddingProjection,
    EntitySummary,
    EntityType,
    ExtractedRecord,
    IngestionJob,
    ListAnchor,
    ProjectionRequest,
    QueryFilters,
    RagAnswer,
    SearchHit,
    SecurityDecision,
    SortDir,
    StatsSummary,
    TokenMatch,
    TokenSuggestion,
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
        tokens: tuple[str, ...] = (),
        token_type: EntityType | None = None,
        token_match: TokenMatch = TokenMatch.ALL,
    ) -> tuple[list[Document], int, ListAnchor | None]:
        """Keyset-paginated documents ordered by ``sort``/``direction`` with ``id`` as tie-breaker.

        ``cursor`` is the ``ListAnchor`` of the last row already seen (None = first page); it must
        match the requested ``sort``/``direction``. Null sort values always sort last. ``tokens``
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
    def get_openai_api_key(self) -> str: ...
    def set_openai_api_key(self, key: str) -> None: ...


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
class StreamingChatModelProvider(Protocol):
    """A chat model that can stream its response (M6.4). Optional capability: the answerer checks
    for it and falls back to ``ChatModelProvider.complete`` when a provider doesn't support it."""

    def stream_complete(self, prompt: str, *, think: bool = False) -> Iterator[ChatChunk]:
        """Yield answer (and, if ``think``, reasoning) chunks as the model generates them."""
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
        reasoning: bool = False,
    ) -> Iterator[ChatEvent]:
        """Streaming variant of ``answer_thread`` (M6.4): yields meta / reasoning / token / sources
        / done events. ``reasoning`` opts into the model's thinking stream."""
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

    def mark_done(self, feature_id: str, *, feature_version: int) -> None: ...
    def mark_failed(self, feature_id: str, *, error: str, next_attempt_at: datetime) -> None: ...
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
