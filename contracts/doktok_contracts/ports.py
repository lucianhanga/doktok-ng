"""Core ports (interfaces) for DokTok NG.

Core domain logic depends on these Protocols, never on concrete adapters (ADR-0001). Adapters in
providers/, storage/, modalities/, and retrieval/ implement them. For M0 these are interface
declarations only; methods are intentionally unimplemented.

See brief section 9 for the full list.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from doktok_contracts.media import (
    ExtractedEntity,
    ExtractedMetadata,
    ExtractedTerm,
    ExtractedTransaction,
    OcrPageResult,
    RenderedPage,
    TextChunk,
)
from doktok_contracts.schemas import (
    AggregationIntent,
    AggregationResult,
    AuditEvent,
    Category,
    CategorySummary,
    Document,
    DocumentArtifact,
    DocumentChunk,
    DocumentEntity,
    DocumentFeature,
    DocumentStatus,
    DocumentVersion,
    EntitySummary,
    EntityType,
    ExtractedRecord,
    IngestionJob,
    RagAnswer,
    SearchHit,
    SecurityDecision,
    StatsSummary,
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
        cursor: tuple[datetime, str] | None = None,
        status: DocumentStatus | None = None,
        category: str | None = None,
        needs_attention: bool = False,
    ) -> tuple[list[Document], int, tuple[datetime, str] | None]:
        """Keyset-paginated documents ordered (created_at DESC, id DESC).

        ``cursor`` is the (created_at, id) of the last row already seen (None = first page).
        ``needs_attention`` keeps only documents with at least one non-done feature; ``category``
        keeps only documents linked to that active category. All filters compose. Returns
        ``(items, total, next_anchor)`` where ``next_anchor`` is None on the last page.
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
    def delete_failed_for_sha(self, tenant_id: str, sha256: str) -> int: ...
    def delete_for_sha(self, tenant_id: str, sha256: str) -> int:
        """Delete all jobs (any status) with this content hash, for a full document purge."""
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


@runtime_checkable
class ChatModelProvider(Protocol):
    def complete(self, prompt: str) -> str: ...


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


@runtime_checkable
class EntityExtractor(Protocol):
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
    def search(self, tenant_id: str, query: str, limit: int = 10) -> list[SearchHit]: ...


@runtime_checkable
class Reranker(Protocol):
    """Reorder retrieved hits by relevance to the query and return the best ``top_k`` (M6.1)."""

    def rerank(self, query: str, hits: list[SearchHit], *, top_k: int) -> list[SearchHit]: ...


@runtime_checkable
class RagAnswerer(Protocol):
    def answer(self, tenant_id: str, question: str, limit: int = 8) -> RagAnswer: ...


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
    def claim_next(
        self, tenant_id: str, *, now: datetime, reclaim_before: datetime
    ) -> DocumentFeature | None: ...
    def mark_done(self, feature_id: str, *, feature_version: int) -> None: ...
    def mark_failed(self, feature_id: str, *, error: str, next_attempt_at: datetime) -> None: ...
    def list_for_document(self, tenant_id: str, document_id: str) -> list[DocumentFeature]: ...
    def list_for_tenant(self, tenant_id: str, *, limit: int = 2000) -> list[DocumentFeature]: ...
    def reset(self, tenant_id: str, document_id: str, feature: str) -> bool: ...


# --- Security -------------------------------------------------------------------------------


@runtime_checkable
class SecurityPolicy(Protocol):
    def is_allowed(self, mime: str, size_bytes: int) -> bool: ...
    def decide(self, mime: str, size_bytes: int) -> SecurityDecision: ...


@runtime_checkable
class QuarantineService(Protocol):
    def quarantine(self, path: str, reason: str) -> None: ...
