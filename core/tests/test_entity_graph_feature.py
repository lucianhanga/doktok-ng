"""EntityGraphFeature: deterministic cross-document entity resolution into graph nodes (KAG P1)."""

from __future__ import annotations

import uuid

from doktok_contracts.schemas import DocumentEntity, EntityType
from doktok_core.entities.inmemory import InMemoryEntityRepository
from doktok_core.features.catalog import FEATURE_CATALOG
from doktok_core.features.processors import EntityGraphFeature
from doktok_core.knowledge_graph.inmemory import InMemoryKnowledgeGraphRepository
from doktok_core.knowledge_graph.resolve import canonical_entity_id


def _mention(
    tenant: str,
    document_id: str,
    entity_type: EntityType,
    normalized_value: str,
    *,
    chunk_id: str | None = None,
) -> DocumentEntity:
    return DocumentEntity(
        id=uuid.uuid4().hex,
        tenant_id=tenant,
        document_id=document_id,
        version_id="",
        chunk_id=chunk_id,
        entity_text=normalized_value,
        entity_type=entity_type,
        normalized_value=normalized_value,
    )


def _feature(
    entities: InMemoryEntityRepository,
) -> tuple[EntityGraphFeature, InMemoryKnowledgeGraphRepository]:
    kg = InMemoryKnowledgeGraphRepository()
    return EntityGraphFeature(entities, kg), kg


def test_resolution_is_idempotent() -> None:
    entities = InMemoryEntityRepository()
    entities.add_entities(
        [
            _mention("t1", "d1", EntityType.PERSON, "Ada Lovelace"),
            _mention("t1", "d1", EntityType.ORG, "Analytical Engine Co"),
        ]
    )
    feature, kg = _feature(entities)

    feature.process("t1", "d1")
    nodes_first = kg.entity_count("t1")
    mentions_first = sorted(
        (m.canonical_entity_id, m.normalized_value) for m in kg.mentions_for_document("t1", "d1")
    )

    feature.process("t1", "d1")  # re-run: must be byte-for-byte the same graph
    assert kg.entity_count("t1") == nodes_first == 2
    mentions_second = sorted(
        (m.canonical_entity_id, m.normalized_value) for m in kg.mentions_for_document("t1", "d1")
    )
    assert mentions_second == mentions_first


def test_cross_document_merge_shares_one_node() -> None:
    entities = InMemoryEntityRepository()
    entities.add_entities(
        [
            _mention("t1", "d1", EntityType.PERSON, "Ada Lovelace"),
            _mention("t1", "d2", EntityType.PERSON, "Ada Lovelace"),
        ]
    )
    feature, kg = _feature(entities)
    feature.process("t1", "d1")
    feature.process("t1", "d2")

    # Two documents naming the same normalized person resolve to ONE canonical node...
    assert kg.entity_count("t1") == 1
    node_id = canonical_entity_id("t1", EntityType.PERSON.value, "Ada Lovelace")
    assert kg.get_entity("t1", node_id) is not None
    # ...with one mention per document linked to it.
    mentions = kg.mentions_for_entity("t1", node_id)
    assert len(mentions) == 2
    assert {m.document_id for m in mentions} == {"d1", "d2"}


def test_never_merges_across_entity_type() -> None:
    entities = InMemoryEntityRepository()
    entities.add_entities(
        [
            _mention("t1", "d1", EntityType.PERSON, "Mercury"),
            _mention("t1", "d1", EntityType.GPE, "Mercury"),
        ]
    )
    feature, kg = _feature(entities)
    feature.process("t1", "d1")

    assert kg.entity_count("t1") == 2  # same surface form, different type -> distinct nodes
    person = canonical_entity_id("t1", EntityType.PERSON.value, "Mercury")
    gpe = canonical_entity_id("t1", EntityType.GPE.value, "Mercury")
    assert person != gpe
    assert kg.get_entity("t1", person) is not None
    assert kg.get_entity("t1", gpe) is not None


def test_tenant_isolation() -> None:
    entities = InMemoryEntityRepository()
    entities.add_entities([_mention("t-a", "d1", EntityType.PERSON, "Ada Lovelace")])
    entities.add_entities([_mention("t-b", "d2", EntityType.PERSON, "Ada Lovelace")])
    feature, kg = _feature(entities)
    feature.process("t-a", "d1")
    feature.process("t-b", "d2")

    # Same name, two tenants -> two distinct nodes; neither tenant can read the other's node.
    a_id = canonical_entity_id("t-a", EntityType.PERSON.value, "Ada Lovelace")
    b_id = canonical_entity_id("t-b", EntityType.PERSON.value, "Ada Lovelace")
    assert a_id != b_id
    assert kg.entity_count("t-a") == 1
    assert kg.entity_count("t-b") == 1
    assert kg.get_entity("t-a", b_id) is None  # cannot reach tenant B's node as tenant A
    assert kg.get_entity("t-b", a_id) is None


def test_excludes_lexical_keyword_tokens() -> None:
    entities = InMemoryEntityRepository()
    entities.add_entities(
        [
            _mention("t1", "d1", EntityType.PERSON, "Ada Lovelace"),
            _mention("t1", "d1", EntityType.CUSTOM_TOKEN, "engine"),
            _mention("t1", "d1", EntityType.CUSTOM_TOKEN, "computation"),
        ]
    )
    feature, kg = _feature(entities)
    feature.process("t1", "d1")

    # Keyword tokens are search aids, not graph entities: only the PERSON becomes a node.
    assert kg.entity_count("t1") == 1
    assert all(m.entity_type == EntityType.PERSON for m in kg.mentions_for_document("t1", "d1"))


def test_reprocess_replaces_stale_mentions() -> None:
    entities = InMemoryEntityRepository()
    entities.add_entities(
        [
            _mention("t1", "d1", EntityType.PERSON, "Ada Lovelace"),
            _mention("t1", "d1", EntityType.ORG, "Analytical Engine Co"),
        ]
    )
    feature, kg = _feature(entities)
    feature.process("t1", "d1")
    assert len(kg.mentions_for_document("t1", "d1")) == 2

    # Re-extraction (entities feature re-ran) produced a different mention set for the document.
    entities.delete_for_document("t1", "d1")
    entities.add_entities([_mention("t1", "d1", EntityType.PERSON, "Ada Lovelace")])
    feature.process("t1", "d1")

    doc_mentions = kg.mentions_for_document("t1", "d1")
    assert len(doc_mentions) == 1
    assert doc_mentions[0].entity_type == EntityType.PERSON


def test_backfill_seeds_graph_from_existing_mentions() -> None:
    # The reconciler-backfill path: a corpus already has document_entities (from prior ingests) and
    # the new feature builds the graph for each document on a fresh run, with no re-extraction.
    entities = InMemoryEntityRepository()
    entities.add_entities(
        [
            _mention("t1", "d1", EntityType.PERSON, "Ada Lovelace"),
            _mention("t1", "d2", EntityType.PERSON, "Ada Lovelace"),
            _mention("t1", "d2", EntityType.ORG, "Analytical Engine Co"),
        ]
    )
    feature, kg = _feature(entities)
    for document_id in ("d1", "d2"):  # reconciler drains one document at a time
        feature.process("t1", document_id)

    assert kg.entity_count("t1") == 2  # one shared PERSON + one ORG
    person = canonical_entity_id("t1", EntityType.PERSON.value, "Ada Lovelace")
    assert {m.document_id for m in kg.mentions_for_entity("t1", person)} == {"d1", "d2"}


def test_feature_is_registered_in_catalog() -> None:
    spec = next((s for s in FEATURE_CATALOG if s.name == EntityGraphFeature.name), None)
    assert spec is not None
    assert spec.version == EntityGraphFeature.version
