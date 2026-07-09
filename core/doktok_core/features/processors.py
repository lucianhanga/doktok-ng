"""Feature processors (ADR-0009): idempotent re-derivation from a document's stored artifacts.

Each reads the active document's canonical artifacts (content.md / content.json), deletes its prior
outputs, and rebuilds them - so the reconciler can (re)run it safely for backfill, retries, or a
version bump. They mirror the inline work done at activation, keyed off the persisted content.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from doktok_contracts.media import LlmUsage
from doktok_contracts.ports import (
    CategoryClassifier,
    CategoryRepository,
    Chunker,
    ChunkRepository,
    DocumentRepository,
    EmbeddingProvider,
    EntityExtractor,
    EntityNerExtractor,
    EntityRepository,
    FileStorage,
    KnowledgeGraphRepository,
    LexicalTermExtractor,
    MetadataExtractor,
    RecordExtractor,
    RecordRepository,
    RelationExtractor,
    Thumbnailer,
)
from doktok_contracts.schemas import (
    DocumentChunk,
    DocumentEntity,
    EntityType,
    ExtractedRecord,
    KgEdge,
    KgEdgeProvenance,
    KgEntity,
    KgEntityMention,
)

from doktok_core.aggregation import normalize_transaction
from doktok_core.aggregation.windowing import stitch_windows, window_text
from doktok_core.documents.artifacts import THUMBNAIL_REL
from doktok_core.enrichment import (
    MAX_CATEGORIES_PER_DOCUMENT,
    MAX_CATEGORIES_PER_TENANT,
    detect_unidentifiable,
    normalize_category,
    normalize_metadata,
)
from doktok_core.entities.language import detect_language, pg_config_for
from doktok_core.entities.lexical import meaningful_terms
from doktok_core.entities.ner import (
    NER_ENTITY_TYPES,
    POSTAL_EVIDENCE_KEY,
    POSTAL_PLACE_KEY,
    POSTAL_PLACE_TYPE_KEY,
    POSTAL_SOURCE_KEY,
    POSTAL_SOURCE_NER,
    normalize_ner_name,
    split_postal_place,
)
from doktok_core.knowledge_graph.predicates import (
    ALLOWED_PREDICATES,
    DETERMINISTIC_PREDICATES,
    PREDICATE_TYPE_PAIRS,
    canonical_edge_id,
)
from doktok_core.knowledge_graph.resolve import KG_NODE_TYPES, canonical_entity_id


def _read_text(file_storage: FileStorage, storage_path: str, name: str) -> str:
    try:
        return file_storage.read_bytes(str(Path(storage_path) / name)).decode("utf-8")
    except FileNotFoundError:
        return ""


def _pages(file_storage: FileStorage, storage_path: str) -> list[str]:
    raw = _read_text(file_storage, storage_path, "content.json")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []  # malformed page data: fall back to content.md upstream rather than crash
    return [str(page.get("text", "")) for page in data.get("pages", [])]


# First-pages budget for cheap LLM enrichment (M8.x #311): title/summary/date/location and the
# document category are well-determined by the opening pages, so feed only those to the model to
# cut tokens/latency/cost. The heavier features (RAG chunking, NER, entities, structured records)
# still read the whole document. Page-aware (a single page can be huge), capped by characters too.
_META_HEAD_PAGES, _META_HEAD_CHARS = 2, 6000
_CLASSIFY_HEAD_PAGES, _CLASSIFY_HEAD_CHARS = 3, 8000


def _head_pages(
    file_storage: FileStorage, storage_path: str, max_pages: int, max_chars: int
) -> str:
    """The first ``max_pages`` pages joined and capped at ``max_chars`` - enough context for cheap
    enrichment without feeding the whole document. Falls back to content.md when page data is
    missing; returns the full (shorter) text unchanged when it is below the cap."""
    pages = _pages(file_storage, storage_path)
    text = (
        "\n\n".join(pages[:max_pages])
        if pages
        else _read_text(file_storage, storage_path, "content.md")
    )
    return text[:max_chars]


# The entity types the rule-based EntitiesFeature owns: everything except the NER types.
_NON_NER_TYPES: list[str] = [t.value for t in EntityType if t not in NER_ENTITY_TYPES]
_NER_TYPES: list[str] = [t.value for t in NER_ENTITY_TYPES]


logger = logging.getLogger("doktok.features")


def _sum_usage(usages: list[LlmUsage]) -> LlmUsage | None:
    """Total the usage of several LLM calls into one (multi-window record extraction). The
    reconciler reads a processor's usage once per document, so a windowed feature reports the sum or
    under-counts tokens/cost. ``estimated`` is sticky; ``eval_ms`` sums only the reported parts."""
    if not usages:
        return None
    eval_ms = [u.eval_ms for u in usages if u.eval_ms is not None]
    return LlmUsage(
        prompt_tokens=sum(u.prompt_tokens for u in usages),
        answer_tokens=sum(u.answer_tokens for u in usages),
        reasoning_tokens=sum(u.reasoning_tokens for u in usages),
        wall_ms=sum(u.wall_ms for u in usages),
        eval_ms=sum(eval_ms) if eval_ms else None,
        estimated=any(u.estimated for u in usages),
    )


def _delegate_usage(provider: object) -> LlmUsage | None:
    """Best-effort read of token usage from an enrichment/embedding provider. Providers that report
    usage expose ``get_last_usage()`` (mirrors UsageReportingChatModel); others -> None. The
    reconciler reads it via the processor's own ``get_last_usage`` after ``process``."""
    getter = getattr(provider, "get_last_usage", None)
    if not callable(getter):
        return None
    result = getter()
    return result if isinstance(result, LlmUsage) else None


def _provider_model(provider: object) -> str:
    """The model name a provider used, when it exposes one (for telemetry); empty otherwise."""
    model = getattr(provider, "model", "")
    return model if isinstance(model, str) else ""


class ChunkEmbedFeature:
    """Re-chunk + re-embed a document into the chunk store (vector + FTS search)."""

    name = "chunk_embed"
    version = 2  # bumped for the qwen3-embedding switch -> reconciler re-embeds the corpus
    dependencies = ("extract",)  # needs extracted content/artifacts

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

    def get_last_usage(self) -> LlmUsage | None:
        return _delegate_usage(self._embedder)

    @property
    def model(self) -> str:
        return _provider_model(self._embedder)

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
    # v4 (#518 Phase 1): validated PHONE/IBAN/VAT_ID/TAX_NUMBER/REGISTRATION_NUMBER (+ADDRESS/
    # POSTAL_CODE with libpostal) -> the reconciler re-extracts the corpus, so values previously
    # only visible as CUSTOM_TOKEN keywords (or missed entirely) get their proper types.
    version = 4
    dependencies = ("extract",)  # needs extracted content/artifacts

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
        # Own every type EXCEPT the NER types (PERSON/ORG/GPE) - those belong to NerFeature, which
        # writes to the same table; scoping the delete lets the two features re-run independently.
        # keep_source: the NER feature also derives POSTAL_CODE rows from PLZ-fused place
        # mentions (#528, marked source="ner") - those are NER-owned and must survive this run.
        self._repo.delete_for_document_types(
            tenant_id, document_id, _NON_NER_TYPES, keep_source=POSTAL_SOURCE_NER
        )
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


class NerFeature:
    """Extract named entities (PERSON/ORG/GPE/JOB_TITLE) via NER, stored as document entities.

    The rule-based ``EntitiesFeature`` cannot find people/organisations/places/job titles (they
    need a model), so this fills them (M7.4; JOB_TITLE is #518 Phase 2). It owns ONLY the NER
    entity types in the shared ``document_entities`` table and replaces just those rows each run,
    so it backfills/retries independently of ``entities``.
    """

    name = "ner"
    version = 1
    dependencies = ("extract",)  # needs extracted content/artifacts

    def __init__(
        self,
        document_repo: DocumentRepository,
        file_storage: FileStorage,
        ner_extractor: EntityNerExtractor,
        entity_repo: EntityRepository,
    ) -> None:
        self._documents = document_repo
        self._files = file_storage
        self._ner = ner_extractor
        self._repo = entity_repo

    def get_last_usage(self) -> LlmUsage | None:
        return _delegate_usage(self._ner)

    @property
    def model(self) -> str:
        return _provider_model(self._ner)

    def process(self, tenant_id: str, document_id: str) -> None:
        document = self._documents.get(tenant_id, document_id)
        if document is None or not document.storage_path:
            return
        # Replace only the NER-owned types so the rule-based entities/keywords are left intact.
        # Plus this feature's OWN postal rows (#528): POSTAL_CODE rows derived from PLZ-fused
        # place mentions carry source="ner"; the rule-based feature's libpostal postal rows do
        # not, so they are never touched here.
        self._repo.delete_for_document_types(tenant_id, document_id, _NER_TYPES)
        self._repo.delete_for_document_types(
            tenant_id,
            document_id,
            [EntityType.POSTAL_CODE.value],
            source=POSTAL_SOURCE_NER,
        )
        content = _read_text(self._files, document.storage_path, "content.md")
        if not content.strip():
            return
        entities = self._aggregate(tenant_id, document_id, content)
        if entities:
            self._repo.add_entities(entities)

    def _aggregate(self, tenant_id: str, document_id: str, text: str) -> list[DocumentEntity]:
        # One row per (type, normalized name); frequency = how often the name occurs in the text so
        # the word cloud can size people/orgs by prominence.
        aggregated: dict[tuple[str, str], DocumentEntity] = {}

        def add(
            entity_type: EntityType,
            entity_text: str,
            normalized: str,
            metadata: dict[str, str] | None = None,
        ) -> None:
            key = (entity_type.value, normalized)
            if not normalized or key in aggregated:
                return
            aggregated[key] = DocumentEntity(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                document_id=document_id,
                version_id="",
                entity_text=entity_text,
                entity_type=entity_type,
                normalized_value=normalized,
                frequency=max(1, text.count(entity_text)),
                metadata=dict(metadata or {}),
            )

        for occ in self._ner.extract(text):
            # PLZ-place split (#528): a German address line "80287 München" arrives as ONE place
            # mention, so every distinct postal code minted its own city node. Peel the code off:
            # the place row keeps just the city (all variants collapse to one node) and the code
            # becomes its own POSTAL_CODE row whose metadata records the pairing - the relation
            # feature turns that into a HAS_POSTAL_CODE edge. Only GPE/LOCATION values split;
            # person/org names are never touched.
            split = (
                split_postal_place(occ.normalized_value)
                if occ.entity_type in (EntityType.GPE, EntityType.LOCATION)
                else None
            )
            if split is None:
                add(occ.entity_type, occ.entity_text, normalize_ner_name(occ.normalized_value))
                continue
            code, place = split
            text_split = split_postal_place(occ.entity_text)
            place_text = text_split[1] if text_split else place
            place_normalized = normalize_ner_name(place)
            add(occ.entity_type, place_text, place_normalized)
            add(
                EntityType.POSTAL_CODE,
                code,
                code,  # digits: casefold/whitespace normalization is a no-op, keep exact
                {
                    POSTAL_SOURCE_KEY: POSTAL_SOURCE_NER,
                    POSTAL_PLACE_KEY: place_normalized,
                    POSTAL_PLACE_TYPE_KEY: occ.entity_type.value,
                    POSTAL_EVIDENCE_KEY: occ.entity_text.strip(),
                },
            )
        return list(aggregated.values())


class EntityGraphFeature:
    """Resolve a document's entity mentions into canonical cross-document graph nodes (KAG Phase 1).

    Reads the document's ``document_entities`` (populated by ``entities`` + ``ner``), maps each
    node-worthy mention to a canonical node whose id is a deterministic function of
    ``(tenant_id, entity_type, normalized_value)``, upserts those nodes, and replaces the document's
    mention links. Two documents naming the same normalized entity therefore share one node with no
    clustering. Idempotent + re-runnable: re-running replaces the document's mentions in place and
    leaves existing nodes untouched, so the reconciler backfills the corpus exactly like the other
    versioned features. Deterministic exact-key only - the pgvector-fuzzy tier is deferred (Phase 2,
    see ``knowledge_graph.resolve``). No LLM, no edges, no retrieval change.
    """

    name = "entity_graph"
    version = 1
    # Runs after both mention producers so the graph reflects every node-worthy entity in the doc.
    dependencies = ("entities", "ner")

    def __init__(
        self,
        entity_repo: EntityRepository,
        knowledge_graph_repo: KnowledgeGraphRepository,
        *,
        node_types: tuple[str, ...] = KG_NODE_TYPES,
    ) -> None:
        self._entities = entity_repo
        self._kg = knowledge_graph_repo
        self._node_types = frozenset(node_types)

    def process(self, tenant_id: str, document_id: str) -> None:
        mentions_src = self._entities.list_for_document(tenant_id, document_id)
        # Alias-aware resolve: a mention of a folded surface form ('M-net') must point straight at
        # its canonical node ('M-net Telekommunikations GmbH'), so an earlier merge survives this
        # re-derivation instead of resurrecting the alias node (KAG alias-folding tier).
        aliases = self._kg.alias_map(tenant_id)
        nodes: dict[str, KgEntity] = {}
        links: list[KgEntityMention] = []
        for mention in mentions_src:
            value = mention.normalized_value
            if mention.entity_type.value not in self._node_types or not value:
                continue
            direct_id = canonical_entity_id(tenant_id, mention.entity_type.value, value)
            resolved_id = aliases.get((mention.entity_type.value, value), direct_id)
            # Only create a node for non-aliased mentions; an aliased mention's canonical node
            # already exists (the alias-table FK guarantees it).
            if resolved_id == direct_id:
                nodes[direct_id] = KgEntity(
                    id=direct_id,
                    tenant_id=tenant_id,
                    entity_type=mention.entity_type,
                    normalized_value=value,
                )
            links.append(
                KgEntityMention(
                    mention_id=mention.id,
                    tenant_id=tenant_id,
                    canonical_entity_id=resolved_id,
                    document_id=document_id,
                    chunk_id=mention.chunk_id,
                    entity_type=mention.entity_type,
                    normalized_value=value,
                )
            )
        # Nodes first (the mentions FK-reference them), then replace this document's mention links.
        self._kg.upsert_entities(list(nodes.values()))
        self._kg.replace_mentions_for_document(tenant_id, document_id, links)


# NER types that ground relation extraction — PERSON/ORG/GPE/LOCATION.
_RELATION_ANCHOR_TYPES: frozenset[str] = frozenset({"PERSON", "ORG", "GPE", "LOCATION"})


class RelationExtractFeature:
    """Extract directed relation triples between named entities (KAG Phase 2).

    Reads the document's NER entities (PERSON/ORG/GPE/LOCATION) as anchor nodes, runs the
    relation extractor over windowed text, validates each triple via the circuit-breaker
    (both endpoints must normalize to a document entity, predicate must be allowed, type pair
    must match, evidence must be non-empty), resolves surviving endpoints to canonical node ids,
    and idempotently replaces this document's edges. No confidence score anywhere.
    """

    name = "relations"
    version = 1
    dependencies = ("entities", "ner", "entity_graph")

    def __init__(
        self,
        document_repo: DocumentRepository,
        file_storage: FileStorage,
        extractor: RelationExtractor,
        entity_repo: EntityRepository,
        knowledge_graph_repo: KnowledgeGraphRepository,
    ) -> None:
        self._documents = document_repo
        self._files = file_storage
        self._extractor = extractor
        self._entities = entity_repo
        self._kg = knowledge_graph_repo

    def get_last_usage(self) -> LlmUsage | None:
        return _delegate_usage(self._extractor)

    @property
    def model(self) -> str:
        return _provider_model(self._extractor)

    def process(self, tenant_id: str, document_id: str) -> None:
        # 1. Load doc text
        doc = self._documents.get(tenant_id, document_id)
        if doc is None or not doc.storage_path:
            return
        content = _read_text(self._files, doc.storage_path, "content.md")
        if not content.strip():
            return

        # 2. Load anchor entities (NER node types only)
        all_entities = self._entities.list_for_document(tenant_id, document_id)
        anchor_entities = [
            e
            for e in all_entities
            if e.entity_type.value in _RELATION_ANCHOR_TYPES and e.normalized_value
        ]
        if not anchor_entities:
            self._kg.replace_edges_for_document(tenant_id, document_id, [], [])
            return

        # 3. Build entity_list: (normalized_value, entity_type) pairs; deduplicated
        entity_set: dict[str, str] = {}  # normalized_value -> entity_type
        for e in anchor_entities:
            if not e.normalized_value:
                continue
            nv = normalize_ner_name(e.normalized_value)
            if nv and nv not in entity_set:
                entity_set[nv] = e.entity_type.value
        entity_list = list(entity_set.items())

        # 4. Extract raw triples
        raw_triples = self._extractor.extract(content, entity_list)

        # 5. Circuit-breaker validation
        valid_triples = []
        dropped = 0
        for triple in raw_triples:
            subj_norm = normalize_ner_name(triple.subject)
            obj_norm = normalize_ner_name(triple.object)
            # Both endpoints must be grounded to document entities
            if subj_norm not in entity_set:
                dropped += 1
                continue
            if obj_norm not in entity_set:
                dropped += 1
                continue
            # Predicate must be in the allowed vocabulary - and NOT deterministic-only: those
            # (e.g. HAS_POSTAL_CODE, #528) are emitted below by code with exact provenance;
            # a model-claimed one is by definition unverified and is dropped.
            if (
                triple.predicate not in ALLOWED_PREDICATES
                or triple.predicate in DETERMINISTIC_PREDICATES
            ):
                dropped += 1
                continue
            # Type pair must match the predicate's allowed pairs
            type_pair = (triple.subject_type, triple.object_type)
            if type_pair not in PREDICATE_TYPE_PAIRS[triple.predicate]:
                dropped += 1
                continue
            # Evidence must be non-empty
            if not triple.evidence.strip():
                dropped += 1
                continue
            valid_triples.append((triple, subj_norm, obj_norm))

        if dropped:
            logger.info(
                "relations %s/%s: dropped %d invalid triples (circuit-breaker)",
                tenant_id,
                document_id,
                dropped,
            )

        # 6. Resolve to canonical entity ids and build KgEdge + KgEdgeProvenance objects.
        # Alias-aware: fold an aliased endpoint to its canonical node so edges attach to the merged
        # node (and never resurrect a folded alias node, which would break the edge's FK).
        aliases = self._kg.alias_map(tenant_id)
        edges_map: dict[str, KgEdge] = {}  # edge_id -> edge
        provenance_rows: list[KgEdgeProvenance] = []
        for triple, subj_norm, obj_norm in valid_triples:
            subj_type = entity_set[subj_norm]
            obj_type = entity_set[obj_norm]
            src_id = aliases.get(
                (subj_type, subj_norm), canonical_entity_id(tenant_id, subj_type, subj_norm)
            )
            dst_id = aliases.get(
                (obj_type, obj_norm), canonical_entity_id(tenant_id, obj_type, obj_norm)
            )
            edge_id = canonical_edge_id(tenant_id, src_id, triple.predicate, dst_id)
            if edge_id not in edges_map:
                edges_map[edge_id] = KgEdge(
                    id=edge_id,
                    tenant_id=tenant_id,
                    src_entity_id=src_id,
                    predicate=triple.predicate,
                    dst_entity_id=dst_id,
                )
            provenance_rows.append(
                KgEdgeProvenance(
                    id=uuid.uuid4().hex,
                    tenant_id=tenant_id,
                    edge_id=edge_id,
                    document_id=document_id,
                    chunk_id=None,  # window-level provenance, no stored chunk id in v1
                    evidence=triple.evidence[:250],
                )
            )

        # 6b. Deterministic HAS_POSTAL_CODE edges (#528): the NER feature split "80287 München"
        # into a place row + a POSTAL_CODE row whose metadata records the pairing and the fused
        # source span; re-link them here as place -> code edges with that span as provenance.
        # Both endpoint nodes exist (entity_graph is a dependency and mints a node per mention).
        self._postal_edges(
            tenant_id, document_id, all_entities, aliases, edges_map, provenance_rows
        )

        logger.info(
            "relations %s/%s: %d raw -> %d valid triples -> %d distinct edges",
            tenant_id,
            document_id,
            len(raw_triples),
            len(valid_triples),
            len(edges_map),
        )

        # 7. Idempotently replace this document's edges
        self._kg.replace_edges_for_document(
            tenant_id, document_id, list(edges_map.values()), provenance_rows
        )

    def _postal_edges(
        self,
        tenant_id: str,
        document_id: str,
        all_entities: list[DocumentEntity],
        aliases: dict[tuple[str, str], str],
        edges_map: dict[str, KgEdge],
        provenance_rows: list[KgEdgeProvenance],
    ) -> None:
        """Append the document's deterministic place -> POSTAL_CODE edges (#528).

        Precision by construction: only POSTAL_CODE rows minted by the NER PLZ-place split
        (source="ner" with a recorded place pairing) qualify, and the code shape is re-checked
        defensively. Alias-aware like the model-extracted edges, so a merged city node keeps
        collecting its codes.
        """
        for entity in all_entities:
            if entity.entity_type is not EntityType.POSTAL_CODE:
                continue
            meta = entity.metadata or {}
            place = meta.get(POSTAL_PLACE_KEY)
            place_type = meta.get(POSTAL_PLACE_TYPE_KEY)
            code = entity.normalized_value or ""
            if (
                meta.get(POSTAL_SOURCE_KEY) != POSTAL_SOURCE_NER
                or not isinstance(place, str)
                or not place
                or place_type not in ("GPE", "LOCATION")
                or not (code.isdigit() and 4 <= len(code) <= 5)  # the PLZ shape, re-checked
            ):
                continue
            src_id = aliases.get(
                (place_type, place), canonical_entity_id(tenant_id, place_type, place)
            )
            dst_id = aliases.get(
                (EntityType.POSTAL_CODE.value, code),
                canonical_entity_id(tenant_id, EntityType.POSTAL_CODE.value, code),
            )
            edge_id = canonical_edge_id(tenant_id, src_id, "HAS_POSTAL_CODE", dst_id)
            if edge_id not in edges_map:
                edges_map[edge_id] = KgEdge(
                    id=edge_id,
                    tenant_id=tenant_id,
                    src_entity_id=src_id,
                    predicate="HAS_POSTAL_CODE",
                    dst_entity_id=dst_id,
                )
            evidence = str(meta.get(POSTAL_EVIDENCE_KEY) or f"{code} {place}")
            provenance_rows.append(
                KgEdgeProvenance(
                    id=uuid.uuid4().hex,
                    tenant_id=tenant_id,
                    edge_id=edge_id,
                    document_id=document_id,
                    chunk_id=None,
                    evidence=evidence[:250],
                )
            )


class DocMetadataFeature:
    """Generate title/date/location/summary via the LLM and store them on the document (M6.2)."""

    name = "doc_metadata"
    version = 2  # bumped for the unidentifiable marker (ADR-0017) -> reconciler re-assesses corpus
    dependencies = ("extract",)  # needs extracted content/artifacts

    def __init__(
        self,
        document_repo: DocumentRepository,
        file_storage: FileStorage,
        metadata_extractor: MetadataExtractor,
    ) -> None:
        self._documents = document_repo
        self._files = file_storage
        self._extractor = metadata_extractor

    def get_last_usage(self) -> LlmUsage | None:
        return _delegate_usage(self._extractor)

    @property
    def model(self) -> str:
        return _provider_model(self._extractor)

    def process(self, tenant_id: str, document_id: str) -> None:
        document = self._documents.get(tenant_id, document_id)
        if document is None or not document.storage_path:
            return
        content = _read_text(self._files, document.storage_path, "content.md")
        if not content.strip():
            return
        # Flag unidentifiable docs from the extracted text deterministically (ADR-0017), before the
        # LLM step, so a meaningless scan is marked even if the model still hallucinates a title.
        self._documents.set_unidentifiable(
            tenant_id, document_id, value=detect_unidentifiable(content)
        )
        # The title/date/location/summary are determined by the opening pages; feed only those to
        # the LLM (#311) - the unidentifiable check above still uses the full content.
        head = _head_pages(self._files, document.storage_path, _META_HEAD_PAGES, _META_HEAD_CHARS)
        meta = normalize_metadata(self._extractor.extract(head))
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
    version = 2  # bumped to honour the unidentifiable marker (ADR-0017)
    # Runs after doc_metadata so the unidentifiable marker is set first: an unidentifiable document
    # gets no categories (it stops the spurious-category pollution at the source).
    dependencies = ("extract", "doc_metadata")

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

    def get_last_usage(self) -> LlmUsage | None:
        return _delegate_usage(self._classifier)

    @property
    def model(self) -> str:
        return _provider_model(self._classifier)

    def process(self, tenant_id: str, document_id: str) -> None:
        document = self._documents.get(tenant_id, document_id)
        if document is None or not document.storage_path:
            return
        # An unidentifiable document gets no categories - and we clear any it accrued before, so
        # re-running this version strips the spurious labels such docs collected previously.
        if document.unidentifiable:
            self._categories.set_document_categories(tenant_id, document_id, [])
            return
        # Categories are well-determined by the opening pages; feed only those to the LLM (#311).
        head = _head_pages(
            self._files, document.storage_path, _CLASSIFY_HEAD_PAGES, _CLASSIFY_HEAD_CHARS
        )
        if not head.strip():
            return
        existing = [c.name for c in self._categories.list_active(tenant_id)]
        labels = self._classifier.classify(head, existing)
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
    version = 2  # bumped for windowed extraction (#314) -> reconciler re-extracts the corpus
    dependencies = ("extract",)  # needs extracted content/artifacts

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
        self._last_usage: LlmUsage | None = None

    def get_last_usage(self) -> LlmUsage | None:
        return self._last_usage

    @property
    def model(self) -> str:
        return _provider_model(self._extractor)

    def process(self, tenant_id: str, document_id: str) -> None:
        self._last_usage = None
        document = self._documents.get(tenant_id, document_id)
        if document is None or not document.storage_path:
            return
        content = _read_text(self._files, document.storage_path, "content.md")
        if not content.strip():
            return
        # Transactions run to the last page, so extract over overlapping windows and stitch the
        # per-window results - a head slice silently dropped everything past ~16k chars (#314).
        windows = window_text(content)
        per_window: list[list[ExtractedRecord]] = []
        usages: list[LlmUsage] = []
        raw_rows = 0
        for window in windows:
            rows = self._extractor.extract(window)
            raw_rows += len(rows)
            usage = _delegate_usage(self._extractor)
            if usage is not None:
                usages.append(usage)
            per_window.append(
                [
                    record
                    for raw in rows
                    if (
                        record := normalize_transaction(
                            raw, tenant_id=tenant_id, document_id=document_id
                        )
                    )
                    is not None
                ]
            )
        records = stitch_windows(per_window)
        self._last_usage = _sum_usage(usages)
        logger.info(
            "structured_records %s/%s: %d windows, %d chars, %d raw rows -> %d records",
            tenant_id,
            document_id,
            len(windows),
            len(content),
            raw_rows,
            len(records),
        )
        self._records.replace_for_document(tenant_id, document_id, records)


class ThumbnailFeature:
    """Render a small first-page preview (WebP) used by the document card and grid/list views.

    Renders from the canonical normalized PDF so it is uniform across born-digital PDFs and OCR'd
    scans. Idempotent: it overwrites the document's ``thumbnails/thumb.webp`` each run, so the
    reconciler can backfill existing documents and re-run on a version bump.
    """

    name = "thumbnail"
    version = 1
    dependencies = ("extract",)  # needs extracted content/artifacts

    def __init__(
        self,
        document_repo: DocumentRepository,
        file_storage: FileStorage,
        thumbnailer: Thumbnailer,
    ) -> None:
        self._documents = document_repo
        self._files = file_storage
        self._thumbnailer = thumbnailer

    def process(self, tenant_id: str, document_id: str) -> None:
        document = self._documents.get(tenant_id, document_id)
        if document is None or not document.storage_path:
            return
        base = Path(document.storage_path)
        # Prefer the normalized system PDF (uniform for PDFs + scans); fall back to the original.
        rel = document.metadata.get("system_document") or document.metadata.get("original")
        source = base / str(rel) if rel else base / document.original_filename
        data = self._thumbnailer.thumbnail(str(source))
        self._files.write_bytes(str(base / THUMBNAIL_REL), data)
