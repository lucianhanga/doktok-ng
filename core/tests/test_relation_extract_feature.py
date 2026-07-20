"""RelationExtractFeature: extract, validate, resolve, and store relation triples (KAG Phase 2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from doktok_contracts.media import ExtractedRelation
from doktok_contracts.schemas import Document, DocumentEntity, DocumentStatus, EntityType
from doktok_core.entities.inmemory import InMemoryEntityRepository
from doktok_core.features.catalog import FEATURE_CATALOG
from doktok_core.features.processors import RelationExtractFeature
from doktok_core.knowledge_graph.inmemory import InMemoryKnowledgeGraphRepository
from doktok_core.knowledge_graph.predicates import canonical_edge_id
from doktok_core.knowledge_graph.resolve import canonical_entity_id

# ---------------------------------------------------------------------------
# Test helpers


class FakeFileStorage:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def read_bytes(self, path: str) -> bytes:
        return self._content

    def move(self, source: str, destination: str) -> None: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    def write_text(self, path: str, text: str) -> None: ...


class FakeRelationExtractor:
    """Returns a fixed list of triples regardless of input."""

    def __init__(self, triples: list[ExtractedRelation]) -> None:
        self._triples = triples

    def extract(self, text: str, entity_list: list[tuple[str, str]]) -> list[ExtractedRelation]:
        return list(self._triples)


class InMemoryDocumentRepository:
    def __init__(self) -> None:
        self._docs: dict[tuple[str, str], Document] = {}

    def add(self, doc: Document) -> None:
        self._docs[(doc.tenant_id, doc.id)] = doc

    def get(self, tenant_id: str, document_id: str) -> Document | None:
        return self._docs.get((tenant_id, document_id))


def _doc(tenant: str = "t1", doc_id: str = "d1") -> Document:
    return Document(
        id=doc_id,
        tenant_id=tenant,
        sha256="x",
        original_filename="doc.pdf",
        status=DocumentStatus.ACTIVE,
        storage_path="/store/" + doc_id,
        created_at=datetime.now(UTC),
    )


def _entity(
    tenant: str,
    doc_id: str,
    entity_type: EntityType,
    normalized_value: str,
) -> DocumentEntity:
    return DocumentEntity(
        id=uuid.uuid4().hex,
        tenant_id=tenant,
        document_id=doc_id,
        version_id="",
        entity_text=normalized_value,
        entity_type=entity_type,
        normalized_value=normalized_value,
    )


def _build(
    triples: list[ExtractedRelation],
    *,
    tenant: str = "t1",
    doc_id: str = "d1",
    entities: list[DocumentEntity] | None = None,
    content: bytes = b"some document text",
) -> tuple[RelationExtractFeature, InMemoryKnowledgeGraphRepository, InMemoryEntityRepository]:
    docs = InMemoryDocumentRepository()
    docs.add(_doc(tenant, doc_id))
    entity_repo = InMemoryEntityRepository()
    if entities:
        entity_repo.add_entities(entities)
    kg = InMemoryKnowledgeGraphRepository()
    feature = RelationExtractFeature(
        docs,  # type: ignore[arg-type]
        FakeFileStorage(content),
        lambda _t: FakeRelationExtractor(triples),
        entity_repo,
        kg,
    )
    return feature, kg, entity_repo


# ---------------------------------------------------------------------------
# Tests


def test_valid_triple_is_accepted() -> None:
    """A well-formed triple with grounded endpoints and valid predicate is stored as an edge."""
    entities = [
        _entity("t1", "d1", EntityType.PERSON, "alice"),
        _entity("t1", "d1", EntityType.ORG, "acme"),
    ]
    triples = [
        ExtractedRelation(
            subject="alice",
            predicate="EMPLOYED_BY",
            object="acme",
            subject_type="PERSON",
            object_type="ORG",
            evidence="Alice works at ACME.",
        )
    ]
    feature, kg, _ = _build(triples, entities=entities)
    feature.process("t1", "d1")
    assert kg.edge_count("t1") == 1


def test_ungrounded_subject_dropped() -> None:
    """A triple whose subject is not in the document's entity set is dropped."""
    entities = [
        _entity("t1", "d1", EntityType.ORG, "acme"),
    ]
    triples = [
        ExtractedRelation(
            subject="bob",  # not in entity set
            predicate="EMPLOYED_BY",
            object="acme",
            subject_type="PERSON",
            object_type="ORG",
            evidence="Bob works at ACME.",
        )
    ]
    feature, kg, _ = _build(triples, entities=entities)
    feature.process("t1", "d1")
    assert kg.edge_count("t1") == 0


def test_ungrounded_object_dropped() -> None:
    """A triple whose object is not in the document's entity set is dropped."""
    entities = [
        _entity("t1", "d1", EntityType.PERSON, "alice"),
    ]
    triples = [
        ExtractedRelation(
            subject="alice",
            predicate="EMPLOYED_BY",
            object="unknowncorp",  # not in entity set
            subject_type="PERSON",
            object_type="ORG",
            evidence="Alice works at UnknownCorp.",
        )
    ]
    feature, kg, _ = _build(triples, entities=entities)
    feature.process("t1", "d1")
    assert kg.edge_count("t1") == 0


def test_invalid_predicate_dropped() -> None:
    """A triple with a predicate not in ALLOWED_PREDICATES is dropped."""
    entities = [
        _entity("t1", "d1", EntityType.PERSON, "alice"),
        _entity("t1", "d1", EntityType.ORG, "acme"),
    ]
    triples = [
        ExtractedRelation(
            subject="alice",
            predicate="HATES",  # not in vocabulary
            object="acme",
            subject_type="PERSON",
            object_type="ORG",
            evidence="Alice hates ACME.",
        )
    ]
    feature, kg, _ = _build(triples, entities=entities)
    feature.process("t1", "d1")
    assert kg.edge_count("t1") == 0


def test_wrong_type_pair_dropped() -> None:
    """A valid predicate with a wrong (subject_type, object_type) pair is dropped."""
    entities = [
        _entity("t1", "d1", EntityType.ORG, "acme"),
        _entity("t1", "d1", EntityType.ORG, "widgets inc"),
    ]
    triples = [
        ExtractedRelation(
            subject="acme",
            predicate="EMPLOYED_BY",  # requires PERSON -> ORG, not ORG -> ORG
            object="widgets inc",
            subject_type="ORG",
            object_type="ORG",
            evidence="ACME is owned by Widgets Inc.",
        )
    ]
    feature, kg, _ = _build(triples, entities=entities)
    feature.process("t1", "d1")
    assert kg.edge_count("t1") == 0


def test_empty_evidence_dropped() -> None:
    """A triple with an empty evidence field is dropped."""
    entities = [
        _entity("t1", "d1", EntityType.PERSON, "alice"),
        _entity("t1", "d1", EntityType.ORG, "acme"),
    ]
    triples = [
        ExtractedRelation(
            subject="alice",
            predicate="EMPLOYED_BY",
            object="acme",
            subject_type="PERSON",
            object_type="ORG",
            evidence="",  # empty
        )
    ]
    feature, kg, _ = _build(triples, entities=entities)
    feature.process("t1", "d1")
    assert kg.edge_count("t1") == 0


def test_endpoints_resolve_to_canonical_ids() -> None:
    """Stored edge src/dst match canonical_entity_id for the anchor entities."""
    entities = [
        _entity("t1", "d1", EntityType.PERSON, "alice"),
        _entity("t1", "d1", EntityType.ORG, "acme"),
    ]
    triples = [
        ExtractedRelation(
            subject="alice",
            predicate="EMPLOYED_BY",
            object="acme",
            subject_type="PERSON",
            object_type="ORG",
            evidence="Alice works at ACME.",
        )
    ]
    feature, kg, _ = _build(triples, entities=entities)
    feature.process("t1", "d1")

    expected_src = canonical_entity_id("t1", "PERSON", "alice")
    expected_dst = canonical_entity_id("t1", "ORG", "acme")
    expected_edge_id = canonical_edge_id("t1", expected_src, "EMPLOYED_BY", expected_dst)

    edges = kg.edges_for_entity("t1", expected_src)
    assert len(edges) == 1
    edge = edges[0]
    assert edge.id == expected_edge_id
    assert edge.src_entity_id == expected_src
    assert edge.dst_entity_id == expected_dst
    assert edge.predicate == "EMPLOYED_BY"


def test_idempotent_replace() -> None:
    """Running the feature twice on the same document produces the same edge count."""
    entities = [
        _entity("t1", "d1", EntityType.PERSON, "alice"),
        _entity("t1", "d1", EntityType.ORG, "acme"),
    ]
    triples = [
        ExtractedRelation(
            subject="alice",
            predicate="EMPLOYED_BY",
            object="acme",
            subject_type="PERSON",
            object_type="ORG",
            evidence="Alice works at ACME.",
        )
    ]
    feature, kg, _ = _build(triples, entities=entities)
    feature.process("t1", "d1")
    count_first = kg.edge_count("t1")
    feature.process("t1", "d1")
    count_second = kg.edge_count("t1")
    assert count_first == count_second == 1


def test_cross_document_edges() -> None:
    """Same triple contributed by two documents -> one edge, two provenance rows."""
    # Set up two documents with the same entities in the same KG.
    docs = InMemoryDocumentRepository()
    docs.add(_doc("t1", "d1"))
    docs.add(_doc("t1", "d2"))
    entity_repo = InMemoryEntityRepository()
    entity_repo.add_entities(
        [
            _entity("t1", "d1", EntityType.PERSON, "alice"),
            _entity("t1", "d1", EntityType.ORG, "acme"),
            _entity("t1", "d2", EntityType.PERSON, "alice"),
            _entity("t1", "d2", EntityType.ORG, "acme"),
        ]
    )
    kg = InMemoryKnowledgeGraphRepository()
    triple = ExtractedRelation(
        subject="alice",
        predicate="EMPLOYED_BY",
        object="acme",
        subject_type="PERSON",
        object_type="ORG",
        evidence="Alice works at ACME.",
    )
    feature = RelationExtractFeature(
        docs,  # type: ignore[arg-type]
        FakeFileStorage(b"text"),
        lambda _t: FakeRelationExtractor([triple]),
        entity_repo,
        kg,
    )
    feature.process("t1", "d1")
    feature.process("t1", "d2")

    # One canonical edge
    assert kg.edge_count("t1") == 1
    src = canonical_entity_id("t1", "PERSON", "alice")
    edges = kg.edges_for_entity("t1", src)
    assert len(edges) == 1
    # evidence_count = 2 (one per document)
    assert edges[0].evidence_count == 2


def test_tenant_isolation() -> None:
    """Two tenants with the same entities produce separate, non-overlapping edges."""
    docs_a = InMemoryDocumentRepository()
    docs_a.add(_doc("t-a", "d1"))
    docs_b = InMemoryDocumentRepository()
    docs_b.add(_doc("t-b", "d1"))
    entity_repo_a = InMemoryEntityRepository()
    entity_repo_a.add_entities(
        [
            _entity("t-a", "d1", EntityType.PERSON, "alice"),
            _entity("t-a", "d1", EntityType.ORG, "acme"),
        ]
    )
    entity_repo_b = InMemoryEntityRepository()
    entity_repo_b.add_entities(
        [
            _entity("t-b", "d1", EntityType.PERSON, "alice"),
            _entity("t-b", "d1", EntityType.ORG, "acme"),
        ]
    )
    kg = InMemoryKnowledgeGraphRepository()
    triple = ExtractedRelation(
        subject="alice",
        predicate="EMPLOYED_BY",
        object="acme",
        subject_type="PERSON",
        object_type="ORG",
        evidence="Alice works at ACME.",
    )
    feature_a = RelationExtractFeature(
        docs_a,  # type: ignore[arg-type]
        FakeFileStorage(b"text"),
        lambda _t: FakeRelationExtractor([triple]),
        entity_repo_a,
        kg,
    )
    feature_b = RelationExtractFeature(
        docs_b,  # type: ignore[arg-type]
        FakeFileStorage(b"text"),
        lambda _t: FakeRelationExtractor([triple]),
        entity_repo_b,
        kg,
    )
    feature_a.process("t-a", "d1")
    feature_b.process("t-b", "d1")

    assert kg.edge_count("t-a") == 1
    assert kg.edge_count("t-b") == 1
    # The two tenants' edges have different canonical ids
    src_a = canonical_entity_id("t-a", "PERSON", "alice")
    src_b = canonical_entity_id("t-b", "PERSON", "alice")
    assert src_a != src_b
    edges_a = kg.edges_for_entity("t-a", src_a)
    edges_b = kg.edges_for_entity("t-b", src_b)
    assert len(edges_a) == 1
    assert len(edges_b) == 1
    assert edges_a[0].id != edges_b[0].id


def test_feature_is_registered_in_catalog() -> None:
    spec = next((s for s in FEATURE_CATALOG if s.name == RelationExtractFeature.name), None)
    assert spec is not None
    assert spec.version == RelationExtractFeature.version
    assert spec.label == "Relation graph"
