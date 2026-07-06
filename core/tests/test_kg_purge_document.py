"""purge_document: pruning a deleted document's orphaned KG nodes + edges (issue #480)."""

from __future__ import annotations

import uuid

from doktok_contracts.schemas import (
    DocumentEntity,
    EntityType,
    KgEdge,
    KgEdgeProvenance,
    KgEntity,
)
from doktok_core.entities.inmemory import InMemoryEntityRepository
from doktok_core.features.processors import EntityGraphFeature
from doktok_core.knowledge_graph.inmemory import InMemoryKnowledgeGraphRepository
from doktok_core.knowledge_graph.predicates import canonical_edge_id
from doktok_core.knowledge_graph.resolve import canonical_entity_id

TENANT = "t1"


def _mention(document_id: str, value: str, etype: EntityType) -> DocumentEntity:
    return DocumentEntity(
        id=uuid.uuid4().hex,
        tenant_id=TENANT,
        document_id=document_id,
        version_id="",
        entity_text=value,
        entity_type=etype,
        normalized_value=value,
    )


def _eid(value: str, etype: EntityType) -> str:
    return canonical_entity_id(TENANT, etype.value, value)


def _build() -> tuple[InMemoryKnowledgeGraphRepository, InMemoryEntityRepository]:
    """d1 -> {alice(PERSON), acme(ORG)}, d2 -> {acme(ORG), bob(PERSON)}; acme is shared."""
    entities = InMemoryEntityRepository()
    entities.add_entities([_mention("d1", "alice", EntityType.PERSON)])
    entities.add_entities([_mention("d1", "acme", EntityType.ORG)])
    entities.add_entities([_mention("d2", "acme", EntityType.ORG)])
    entities.add_entities([_mention("d2", "bob", EntityType.PERSON)])
    kg = InMemoryKnowledgeGraphRepository()
    feature = EntityGraphFeature(entities, kg)
    feature.process(TENANT, "d1")
    feature.process(TENANT, "d2")
    # An edge alice --works_at--> acme, sourced only from d1.
    src, dst = _eid("alice", EntityType.PERSON), _eid("acme", EntityType.ORG)
    edge_id = canonical_edge_id(TENANT, src, "works_at", dst)
    kg.replace_edges_for_document(
        TENANT,
        "d1",
        [
            KgEdge(
                id=edge_id,
                tenant_id=TENANT,
                src_entity_id=src,
                predicate="works_at",
                dst_entity_id=dst,
            )
        ],
        [
            KgEdgeProvenance(
                id=uuid.uuid4().hex,
                tenant_id=TENANT,
                edge_id=edge_id,
                document_id="d1",
                evidence="alice works at acme",
            )
        ],
    )
    return kg, entities


def test_purge_document_prunes_orphaned_node_but_keeps_shared() -> None:
    kg, _ = _build()
    assert kg.entity_count(TENANT) == 3  # alice, acme, bob
    assert kg.edge_count(TENANT) == 1

    # Simulate the delete cascade: the doc's mentions are removed (document_entities -> mentions).
    kg.replace_mentions_for_document(TENANT, "d1", [])
    pruned = kg.purge_document(TENANT, "d1")

    assert pruned == 1  # alice (only in d1) is orphaned
    assert kg.get_entity(TENANT, _eid("alice", EntityType.PERSON)) is None
    assert kg.get_entity(TENANT, _eid("acme", EntityType.ORG)) is not None  # shared via d2
    assert kg.get_entity(TENANT, _eid("bob", EntityType.PERSON)) is not None
    # The edge lost its only provenance (d1) and its src node is gone -> pruned.
    assert kg.edge_count(TENANT) == 0


def test_purge_document_is_idempotent() -> None:
    kg, _ = _build()
    kg.replace_mentions_for_document(TENANT, "d1", [])
    assert kg.purge_document(TENANT, "d1") == 1
    assert kg.purge_document(TENANT, "d1") == 0  # nothing left to prune


def test_purge_document_clears_preexisting_orphans_tenant_wide() -> None:
    kg = InMemoryKnowledgeGraphRepository()
    # A canonical node with no mentions at all (e.g. left behind by an earlier pre-fix delete).
    orphan = KgEntity(
        id=_eid("ghost", EntityType.ORG),
        tenant_id=TENANT,
        entity_type=EntityType.ORG,
        normalized_value="ghost",
    )
    kg.upsert_entities([orphan])
    assert kg.entity_count(TENANT) == 1
    # Purging any document sweeps all current orphans for the tenant.
    assert kg.purge_document(TENANT, "unrelated-doc") == 1
    assert kg.entity_count(TENANT) == 0
