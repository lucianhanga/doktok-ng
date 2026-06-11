"""Feature processors (ADR-0009): idempotent re-derivation from a document's stored artifacts.

Each reads the active document's canonical artifacts (content.md / content.json), deletes its prior
outputs, and rebuilds them - so the reconciler can (re)run it safely for backfill, retries, or a
version bump. They mirror the inline work done at activation, keyed off the persisted content.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from doktok_contracts.ports import (
    CategoryClassifier,
    CategoryRepository,
    Chunker,
    ChunkRepository,
    DocumentRepository,
    EmbeddingProvider,
    EntityExtractor,
    EntityRepository,
    FileStorage,
    LexicalTermExtractor,
    MetadataExtractor,
    RecordExtractor,
    RecordRepository,
)
from doktok_contracts.schemas import DocumentChunk, DocumentEntity, EntityType, ExtractedRecord

from doktok_core.aggregation import normalize_transaction
from doktok_core.enrichment import (
    MAX_CATEGORIES_PER_DOCUMENT,
    MAX_CATEGORIES_PER_TENANT,
    normalize_category,
    normalize_metadata,
)
from doktok_core.entities.language import detect_language, pg_config_for
from doktok_core.entities.lexical import meaningful_terms


def _read_text(file_storage: FileStorage, storage_path: str, name: str) -> str:
    try:
        return file_storage.read_bytes(str(Path(storage_path) / name)).decode("utf-8")
    except FileNotFoundError:
        return ""


def _pages(file_storage: FileStorage, storage_path: str) -> list[str]:
    raw = _read_text(file_storage, storage_path, "content.json")
    if not raw:
        return []
    data = json.loads(raw)
    return [str(page.get("text", "")) for page in data.get("pages", [])]


class ChunkEmbedFeature:
    """Re-chunk + re-embed a document into the chunk store (vector + FTS search)."""

    name = "chunk_embed"
    version = 2  # bumped for the qwen3-embedding switch -> reconciler re-embeds the corpus

    def __init__(
        self,
        document_repo: DocumentRepository,
        file_storage: FileStorage,
        chunker: Chunker,
        embedding_provider: EmbeddingProvider,
        chunk_repo: ChunkRepository,
    ) -> None:
        self._documents = document_repo
        self._files = file_storage
        self._chunker = chunker
        self._embedder = embedding_provider
        self._chunks = chunk_repo

    def process(self, tenant_id: str, document_id: str) -> None:
        document = self._documents.get(tenant_id, document_id)
        if document is None or not document.storage_path:
            return
        self._chunks.delete_for_document(tenant_id, document_id)
        chunks: list[DocumentChunk] = []
        for page_number, page_text in enumerate(self._pages(document.storage_path), start=1):
            for piece in self._chunker.chunk(page_text):
                chunks.append(
                    DocumentChunk(
                        id=uuid.uuid4().hex,
                        tenant_id=tenant_id,
                        document_id=document_id,
                        version_id="",
                        page_start=page_number,
                        page_end=page_number,
                        heading_path=[],
                        text=piece.text,
                        token_count=piece.token_count,
                        metadata={
                            "start_offset": piece.start_offset,
                            "end_offset": piece.end_offset,
                        },
                    )
                )
        if chunks:
            embeddings = self._embedder.embed([chunk.text for chunk in chunks])
            self._chunks.add_chunks(chunks, embeddings)

    def _pages(self, storage_path: str) -> list[str]:
        return _pages(self._files, storage_path)


class EntitiesFeature:
    """Re-extract structured entities + multilingual lexical terms for a document."""

    name = "entities"
    version = 3  # bumped for plausibility-filtered lexical terms -> reconciler re-extracts corpus

    def __init__(
        self,
        document_repo: DocumentRepository,
        file_storage: FileStorage,
        entity_extractor: EntityExtractor,
        lexical_term_extractor: LexicalTermExtractor,
        entity_repo: EntityRepository,
        *,
        lexical_terms_limit: int = 200,
    ) -> None:
        self._documents = document_repo
        self._files = file_storage
        self._entities = entity_extractor
        self._lexical = lexical_term_extractor
        self._repo = entity_repo
        self._lexical_terms_limit = lexical_terms_limit

    def process(self, tenant_id: str, document_id: str) -> None:
        document = self._documents.get(tenant_id, document_id)
        if document is None or not document.storage_path:
            return
        content = _read_text(self._files, document.storage_path, "content.md")
        self._repo.delete_for_document(tenant_id, document_id)
        entities = self._structured(tenant_id, document_id, content)
        entities.extend(self._terms(tenant_id, document_id, content))
        if entities:
            self._repo.add_entities(entities)

    def _structured(self, tenant_id: str, document_id: str, text: str) -> list[DocumentEntity]:
        aggregated: dict[tuple[str, str], DocumentEntity] = {}
        for occ in self._entities.extract(text):
            key = (occ.entity_type.value, occ.normalized_value)
            existing = aggregated.get(key)
            if existing is None:
                aggregated[key] = DocumentEntity(
                    id=uuid.uuid4().hex,
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_id="",
                    entity_text=occ.entity_text,
                    entity_type=occ.entity_type,
                    normalized_value=occ.normalized_value,
                    frequency=1,
                )
            else:
                existing.frequency += 1
        return list(aggregated.values())

    def _terms(self, tenant_id: str, document_id: str, text: str) -> list[DocumentEntity]:
        language = detect_language(text)
        config = pg_config_for(language)
        limit = self._lexical_terms_limit
        # Over-fetch candidates, then keep only plausible words (drops OCR/markup/script noise).
        candidates = self._lexical.extract_terms(text, config=config, limit=limit * 4)
        terms = meaningful_terms(candidates, language=language, limit=limit)
        return [
            DocumentEntity(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                document_id=document_id,
                version_id="",
                entity_text=term.term,
                entity_type=EntityType.CUSTOM_TOKEN,
                normalized_value=term.term,
                frequency=term.frequency,
                metadata={"language": language},
            )
            for term in terms
        ]


class DocMetadataFeature:
    """Generate title/date/location/summary via the LLM and store them on the document (M6.2)."""

    name = "doc_metadata"
    version = 1

    def __init__(
        self,
        document_repo: DocumentRepository,
        file_storage: FileStorage,
        metadata_extractor: MetadataExtractor,
    ) -> None:
        self._documents = document_repo
        self._files = file_storage
        self._extractor = metadata_extractor

    def process(self, tenant_id: str, document_id: str) -> None:
        document = self._documents.get(tenant_id, document_id)
        if document is None or not document.storage_path:
            return
        content = _read_text(self._files, document.storage_path, "content.md")
        if not content.strip():
            return
        meta = normalize_metadata(self._extractor.extract(content))
        self._documents.set_metadata(
            tenant_id,
            document_id,
            title=meta.title,
            document_date=meta.document_date,
            location=meta.location,
            summary=meta.summary,
        )


class DocClassifyFeature:
    """Assign multi-label categories from a bounded controlled vocabulary (M6.2).

    The LLM proposes labels; this resolves each against the live taxonomy (exact -> fuzzy -> create
    if under the cap -> else nearest existing), so the prompt is best-effort and the caps are the
    guarantee. Idempotent: it replaces the document's category links each run.
    """

    name = "doc_classify"
    version = 1

    def __init__(
        self,
        document_repo: DocumentRepository,
        file_storage: FileStorage,
        classifier: CategoryClassifier,
        category_repo: CategoryRepository,
    ) -> None:
        self._documents = document_repo
        self._files = file_storage
        self._classifier = classifier
        self._categories = category_repo

    def process(self, tenant_id: str, document_id: str) -> None:
        document = self._documents.get(tenant_id, document_id)
        if document is None or not document.storage_path:
            return
        content = _read_text(self._files, document.storage_path, "content.md")
        if not content.strip():
            return
        existing = [c.name for c in self._categories.list_active(tenant_id)]
        labels = self._classifier.classify(content, existing)
        category_ids: list[str] = []
        for label in labels:
            if len(category_ids) >= MAX_CATEGORIES_PER_DOCUMENT:
                break
            category = self._resolve(tenant_id, label)
            if category is not None and category.id not in category_ids:
                category_ids.append(category.id)
        self._categories.set_document_categories(tenant_id, document_id, category_ids)

    def _resolve(self, tenant_id: str, label: str):  # type: ignore[no-untyped-def]
        normalized = normalize_category(label)
        if not normalized:
            return None
        existing = self._categories.find_by_normalized(tenant_id, normalized)
        if existing is not None:
            return existing
        similar = self._categories.find_similar(tenant_id, normalized)
        if similar is not None:
            return similar
        if self._categories.active_count(tenant_id) < MAX_CATEGORIES_PER_TENANT:
            created = self._categories.create(tenant_id, label.strip(), normalized)
            if created is not None:
                return created
        # At the cap (or lost the create race): force-pick the nearest existing category.
        return self._categories.find_nearest(tenant_id, normalized)


class StructuredRecordsFeature:
    """Extract typed line items (transactions) into the queryable record store (M6.3).

    Runs on every active document; the extractor returns nothing for non-financial documents.
    Idempotent: it replaces the document's records each run.
    """

    name = "structured_records"
    version = 1

    def __init__(
        self,
        document_repo: DocumentRepository,
        file_storage: FileStorage,
        record_extractor: RecordExtractor,
        record_repo: RecordRepository,
    ) -> None:
        self._documents = document_repo
        self._files = file_storage
        self._extractor = record_extractor
        self._records = record_repo

    def process(self, tenant_id: str, document_id: str) -> None:
        document = self._documents.get(tenant_id, document_id)
        if document is None or not document.storage_path:
            return
        content = _read_text(self._files, document.storage_path, "content.md")
        if not content.strip():
            return
        records: list[ExtractedRecord] = []
        for raw in self._extractor.extract(content):
            record = normalize_transaction(raw, tenant_id=tenant_id, document_id=document_id)
            if record is not None:
                records.append(record)
        self._records.replace_for_document(tenant_id, document_id, records)
