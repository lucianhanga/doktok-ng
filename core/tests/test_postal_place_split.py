"""PLZ-place split (#528): "80287 München" must become ONE city node + POSTAL_CODE nodes + edges.

German address lines fuse postal code and city, and NER returns the whole span as one GPE
mention - so every distinct PLZ minted its own city node (~15 fake Münchens). The split peels
the code off at NER-aggregation time; the entity graph then collapses all variants into one
city node, and the relation feature emits deterministic HAS_POSTAL_CODE edges.

Precision-first: the negatives here pin the guardrails - under-splitting is always preferred
to wrong-splitting.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from doktok_contracts.media import ExtractedEntity, ExtractedRelation
from doktok_contracts.schemas import Document, DocumentEntity, DocumentStatus, EntityType
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.entities.inmemory import InMemoryEntityRepository
from doktok_core.entities.ner import (
    POSTAL_EVIDENCE_KEY,
    POSTAL_PLACE_KEY,
    POSTAL_PLACE_TYPE_KEY,
    POSTAL_SOURCE_KEY,
    POSTAL_SOURCE_NER,
    split_postal_place,
)
from doktok_core.features.processors import (
    EntityGraphFeature,
    NerFeature,
    RelationExtractFeature,
)
from doktok_core.knowledge_graph.inmemory import InMemoryKnowledgeGraphRepository
from doktok_core.knowledge_graph.predicates import (
    ALLOWED_PREDICATES,
    DETERMINISTIC_PREDICATES,
    PREDICATE_TYPE_PAIRS,
    canonical_edge_id,
)
from doktok_core.knowledge_graph.resolve import canonical_entity_id

# ---------------------------------------------------------------------------
# Test helpers (mirroring test_ner_feature.py / test_relation_extract_feature.py)


class FakeFileStorage:
    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    def read_bytes(self, path: str) -> bytes:
        try:
            return self._files[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    def move(self, source: str, destination: str) -> None: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    def write_text(self, path: str, text: str) -> None: ...


class FakeNer:
    def __init__(self, entities: list[ExtractedEntity]) -> None:
        self._entities = entities

    def extract(self, text: str) -> list[ExtractedEntity]:
        return self._entities


class FakeRelationExtractor:
    def __init__(self, triples: list[ExtractedRelation] | None = None) -> None:
        self._triples = triples or []

    def extract(self, text: str, entity_list: list[tuple[str, str]]) -> list[ExtractedRelation]:
        return list(self._triples)


def _occ(text: str, t: EntityType) -> ExtractedEntity:
    return ExtractedEntity(
        entity_text=text, entity_type=t, normalized_value=text, start_offset=0, end_offset=0
    )


def _doc(doc_id: str = "d1") -> Document:
    return Document(
        id=doc_id,
        tenant_id="t1",
        sha256="x",
        original_filename="letter.pdf",
        title="letter",
        status=DocumentStatus.ACTIVE,
        storage_path="/store/" + doc_id,
        created_at=datetime.now(UTC),
    )


def _run_ner(content: bytes, occurrences: list[ExtractedEntity]) -> InMemoryEntityRepository:
    docs = InMemoryDocumentRepository()
    docs.add(_doc())
    files = FakeFileStorage({"/store/d1/content.md": content})
    shared = InMemoryEntityRepository()
    NerFeature(docs, files, lambda _t: FakeNer(occurrences), shared).process("t1", "d1")
    return shared


def _by_key(repo: InMemoryEntityRepository) -> dict[tuple[EntityType, str | None], DocumentEntity]:
    return {(e.entity_type, e.normalized_value): e for e in repo.list_for_document("t1", "d1")}


# ---------------------------------------------------------------------------
# split_postal_place: the guardrails


def test_split_positive_german_plz() -> None:
    assert split_postal_place("80287 München") == ("80287", "München")


def test_split_positive_four_digit_plz_and_whitespace() -> None:
    # Austrian/Swiss 4-digit codes and ragged whitespace both split.
    assert split_postal_place("  1010  Wien ") == ("1010", "Wien")


def test_split_negative_bare_digit_run() -> None:
    # A bare 5-digit run is not a place - never invent a city from it.
    assert split_postal_place("80287") is None


def test_split_negative_no_leading_digits() -> None:
    assert split_postal_place("München") is None


def test_split_negative_mid_string_number() -> None:
    # Digits that are not at the very start are left alone.
    assert split_postal_place("Bad 80287 München") is None
    assert split_postal_place("München 80287") is None


def test_split_negative_wrong_digit_count() -> None:
    # 1-3 or 6+ leading digits are not a PLZ shape.
    assert split_postal_place("4 Trees Valley") is None
    assert split_postal_place("123 Main Street") is None
    assert split_postal_place("123456 Foo") is None


def test_split_negative_digit_initial_remainder() -> None:
    # The remainder must start with a letter ("80287 2nd" could be a street number run).
    assert split_postal_place("80287 2nd Avenue") is None


def test_split_negative_multiline_value() -> None:
    assert split_postal_place("80287 München\nBayern") is None


# ---------------------------------------------------------------------------
# NerFeature: split emission + negatives


def test_plz_place_mention_splits_into_city_and_postal_code() -> None:
    repo = _run_ner(b"Adresse: 80287 M\xc3\xbcnchen.", [_occ("80287 München", EntityType.GPE)])
    stored = _by_key(repo)
    # The place row is the CITY - not the fused "80287 münchen" that minted per-PLZ nodes.
    assert (EntityType.GPE, "münchen") in stored
    assert (EntityType.GPE, "80287 münchen") not in stored
    # The code became its own POSTAL_CODE row with the pairing + provenance metadata.
    postal = stored[(EntityType.POSTAL_CODE, "80287")]
    assert postal.metadata[POSTAL_SOURCE_KEY] == POSTAL_SOURCE_NER
    assert postal.metadata[POSTAL_PLACE_KEY] == "münchen"
    assert postal.metadata[POSTAL_PLACE_TYPE_KEY] == "GPE"
    assert postal.metadata[POSTAL_EVIDENCE_KEY] == "80287 München"


def test_many_plz_variants_collapse_to_one_city() -> None:
    repo = _run_ner(
        b"80287 M\xc3\xbcnchen und 80333 M\xc3\xbcnchen",
        [_occ("80287 München", EntityType.GPE), _occ("80333 München", EntityType.GPE)],
    )
    rows = repo.list_for_document("t1", "d1")
    gpe = [e for e in rows if e.entity_type is EntityType.GPE]
    postal = {e.normalized_value for e in rows if e.entity_type is EntityType.POSTAL_CODE}
    # ONE city row (identical normalized value -> one canonical node) + two distinct codes.
    assert [e.normalized_value for e in gpe] == ["münchen"]
    assert postal == {"80287", "80333"}


def test_person_names_are_never_split() -> None:
    # The split applies ONLY to GPE/LOCATION mentions - a person name is left intact even if
    # it happens to match the shape.
    repo = _run_ner(b"x", [_occ("80287 Marcus", EntityType.PERSON)])
    stored = _by_key(repo)
    assert (EntityType.PERSON, "80287 marcus") in stored
    assert not any(t is EntityType.POSTAL_CODE for t, _ in stored)


def test_non_matching_gpe_values_stay_unchanged() -> None:
    repo = _run_ner(
        b"x",
        [
            _occ("80287", EntityType.GPE),  # bare digits: no city to split out
            _occ("Bad 80287 Aibling", EntityType.GPE),  # mid-string number
            _occ("80287 2nd Avenue", EntityType.GPE),  # digit-initial remainder
        ],
    )
    stored = _by_key(repo)
    assert (EntityType.GPE, "80287") in stored
    assert (EntityType.GPE, "bad 80287 aibling") in stored
    assert (EntityType.GPE, "80287 2nd avenue") in stored
    assert not any(t is EntityType.POSTAL_CODE for t, _ in stored)


# ---------------------------------------------------------------------------
# Ownership: the two POSTAL_CODE producers never clobber or duplicate each other


def _libpostal_row(doc_id: str = "d1") -> DocumentEntity:
    # A rule-based (libpostal address component) postal row: NO source marker.
    return DocumentEntity(
        id=uuid.uuid4().hex,
        tenant_id="t1",
        document_id=doc_id,
        version_id="",
        entity_text="99999",
        entity_type=EntityType.POSTAL_CODE,
        normalized_value="99999",
        frequency=1,
    )


def test_ner_rerun_is_idempotent_and_keeps_libpostal_postal_rows() -> None:
    docs = InMemoryDocumentRepository()
    docs.add(_doc())
    files = FakeFileStorage({"/store/d1/content.md": b"80287 M\xc3\xbcnchen"})
    shared = InMemoryEntityRepository()
    shared.add_entities([_libpostal_row()])
    feature = NerFeature(
        docs, files, lambda _t: FakeNer([_occ("80287 München", EntityType.GPE)]), shared
    )
    feature.process("t1", "d1")
    feature.process("t1", "d1")  # re-run: replaces its own postal rows, no duplicates

    postal = [
        e for e in shared.list_for_document("t1", "d1") if e.entity_type is EntityType.POSTAL_CODE
    ]
    assert sorted(e.normalized_value or "" for e in postal) == ["80287", "99999"]


def test_rule_based_delete_scope_keeps_ner_postal_rows() -> None:
    # EntitiesFeature deletes its own types with keep_source="ner": the libpostal row goes,
    # the NER-derived row survives.
    shared = _run_ner(b"80287 M\xc3\xbcnchen", [_occ("80287 München", EntityType.GPE)])
    shared.add_entities([_libpostal_row()])
    shared.delete_for_document_types(
        "t1", "d1", [EntityType.POSTAL_CODE.value], keep_source=POSTAL_SOURCE_NER
    )
    postal = [
        e for e in shared.list_for_document("t1", "d1") if e.entity_type is EntityType.POSTAL_CODE
    ]
    assert [e.normalized_value for e in postal] == ["80287"]


# ---------------------------------------------------------------------------
# End-to-end: ner -> entity_graph -> relations


def test_pipeline_one_city_node_many_postal_nodes_and_edges() -> None:
    docs = InMemoryDocumentRepository()
    docs.add(_doc())
    files = FakeFileStorage({"/store/d1/content.md": b"80287 M\xc3\xbcnchen, 80333 M\xc3\xbcnchen"})
    shared = InMemoryEntityRepository()
    kg = InMemoryKnowledgeGraphRepository()
    ner = FakeNer([_occ("80287 München", EntityType.GPE), _occ("80333 München", EntityType.GPE)])
    NerFeature(docs, files, lambda _t: ner, shared).process("t1", "d1")
    EntityGraphFeature(shared, kg).process("t1", "d1")
    RelationExtractFeature(
        docs,
        files,
        lambda _t: FakeRelationExtractor(),
        shared,
        kg,
    ).process("t1", "d1")

    # ONE city node, TWO postal nodes.
    nodes = kg.list_entities("t1")
    cities = [n for n in nodes if n.entity_type is EntityType.GPE]
    codes = sorted(n.normalized_value for n in nodes if n.entity_type is EntityType.POSTAL_CODE)
    assert [n.normalized_value for n in cities] == ["münchen"]
    assert codes == ["80287", "80333"]

    # TWO HAS_POSTAL_CODE edges city -> code, with the fused span as provenance evidence.
    city_id = canonical_entity_id("t1", "GPE", "münchen")
    edges = kg.edges_for_entity("t1", city_id)
    assert len(edges) == 2
    assert {e.predicate for e in edges} == {"HAS_POSTAL_CODE"}
    assert {e.src_entity_id for e in edges} == {city_id}
    assert {e.dst_entity_id for e in edges} == {
        canonical_entity_id("t1", "POSTAL_CODE", "80287"),
        canonical_entity_id("t1", "POSTAL_CODE", "80333"),
    }
    expected_edge = canonical_edge_id(
        "t1", city_id, "HAS_POSTAL_CODE", canonical_entity_id("t1", "POSTAL_CODE", "80287")
    )
    assert expected_edge in {e.id for e in edges}
    _, provenance = kg.neighborhood("t1", [city_id])
    assert {p.evidence for p in provenance} == {"80287 München", "80333 München"}


def test_relations_rerun_keeps_postal_edges_idempotent() -> None:
    docs = InMemoryDocumentRepository()
    docs.add(_doc())
    files = FakeFileStorage({"/store/d1/content.md": b"80287 M\xc3\xbcnchen"})
    shared = InMemoryEntityRepository()
    kg = InMemoryKnowledgeGraphRepository()
    NerFeature(
        docs, files, lambda _t: FakeNer([_occ("80287 München", EntityType.GPE)]), shared
    ).process("t1", "d1")
    EntityGraphFeature(shared, kg).process("t1", "d1")
    relations = RelationExtractFeature(
        docs,
        files,
        lambda _t: FakeRelationExtractor(),
        shared,
        kg,
    )
    relations.process("t1", "d1")
    relations.process("t1", "d1")
    assert kg.edge_count("t1") == 1


# ---------------------------------------------------------------------------
# Predicate vocabulary: pairs accepted/rejected, deterministic-only enforcement


def test_has_postal_code_type_pairs() -> None:
    assert "HAS_POSTAL_CODE" in ALLOWED_PREDICATES
    assert "HAS_POSTAL_CODE" in DETERMINISTIC_PREDICATES
    pairs = PREDICATE_TYPE_PAIRS["HAS_POSTAL_CODE"]
    # Direction: the PLACE is the subject ("München HAS_POSTAL_CODE 80287").
    assert ("GPE", "POSTAL_CODE") in pairs
    assert ("LOCATION", "POSTAL_CODE") in pairs
    assert ("PERSON", "POSTAL_CODE") not in pairs
    assert ("POSTAL_CODE", "GPE") not in pairs


def test_model_claimed_has_postal_code_triple_is_dropped() -> None:
    # HAS_POSTAL_CODE is deterministic-only: a model-produced triple claiming it (even a
    # well-typed one) is dropped by the circuit-breaker; only the split path emits the edge.
    docs = InMemoryDocumentRepository()
    docs.add(_doc())
    files = FakeFileStorage({"/store/d1/content.md": b"M\xc3\xbcnchen"})
    shared = InMemoryEntityRepository()
    shared.add_entities(
        [
            DocumentEntity(
                id=uuid.uuid4().hex,
                tenant_id="t1",
                document_id="d1",
                version_id="",
                entity_text="münchen",
                entity_type=EntityType.GPE,
                normalized_value="münchen",
            )
        ]
    )
    kg = InMemoryKnowledgeGraphRepository()
    triple = ExtractedRelation(
        subject="münchen",
        predicate="HAS_POSTAL_CODE",
        object="80287",
        subject_type="GPE",
        object_type="POSTAL_CODE",
        evidence="80287 München",
    )
    RelationExtractFeature(
        docs,
        files,
        lambda _t: FakeRelationExtractor([triple]),
        shared,
        kg,
    ).process("t1", "d1")
    assert kg.edge_count("t1") == 0
