"""Alias-resolution pass against the in-memory KG repo: stability, idempotency, edges, isolation."""

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
from doktok_core.knowledge_graph.alias import resolve_tenant_aliases
from doktok_core.knowledge_graph.inmemory import InMemoryKnowledgeGraphRepository
from doktok_core.knowledge_graph.predicates import canonical_edge_id
from doktok_core.knowledge_graph.resolve import canonical_entity_id

MNET = "M-net"
MNET_FULL = "M-net Telekommunikations GmbH"


def _mention(tenant: str, document_id: str, value: str) -> DocumentEntity:
    return DocumentEntity(
        id=uuid.uuid4().hex,
        tenant_id=tenant,
        document_id=document_id,
        version_id="",
        entity_text=value,
        entity_type=EntityType.ORG,
        normalized_value=value,
    )


def test_alias_pass_folds_and_repoints_mentions() -> None:
    tenant = "t1"
    entities = InMemoryEntityRepository()
    entities.add_entities([_mention(tenant, "d1", MNET)])
    entities.add_entities([_mention(tenant, "d2", MNET_FULL)])
    kg = InMemoryKnowledgeGraphRepository()
    feature = EntityGraphFeature(entities, kg)
    feature.process(tenant, "d1")
    feature.process(tenant, "d2")
    assert kg.entity_count(tenant) == 2

    merged = resolve_tenant_aliases(kg, tenant)
    assert merged == 1
    assert kg.entity_count(tenant) == 1  # the conservative 2 -> 1 merge

    canonical = canonical_entity_id(tenant, "ORG", MNET_FULL)
    assert kg.get_entity(tenant, canonical) is not None
    assert kg.get_entity(tenant, canonical_entity_id(tenant, "ORG", MNET)) is None  # alias gone
    # Both documents' mentions now resolve to the single canonical node.
    assert {m.document_id for m in kg.mentions_for_entity(tenant, canonical)} == {"d1", "d2"}


def test_merge_is_stable_across_reprocessing() -> None:
    # The crux: after a merge, re-running EntityGraphFeature on the alias document must keep it
    # pointed at the canonical node (no node resurrection), because resolve is alias-aware.
    tenant = "t1"
    entities = InMemoryEntityRepository()
    entities.add_entities([_mention(tenant, "d1", MNET)])
    entities.add_entities([_mention(tenant, "d2", MNET_FULL)])
    kg = InMemoryKnowledgeGraphRepository()
    feature = EntityGraphFeature(entities, kg)
    feature.process(tenant, "d1")
    feature.process(tenant, "d2")
    resolve_tenant_aliases(kg, tenant)
    canonical = canonical_entity_id(tenant, "ORG", MNET_FULL)

    feature.process(tenant, "d1")  # reprocess the alias document
    assert kg.entity_count(tenant) == 1  # alias node was NOT resurrected
    doc_mentions = kg.mentions_for_document(tenant, "d1")
    assert len(doc_mentions) == 1
    assert doc_mentions[0].canonical_entity_id == canonical


def test_alias_pass_is_idempotent() -> None:
    tenant = "t1"
    entities = InMemoryEntityRepository()
    entities.add_entities([_mention(tenant, "d1", MNET)])
    entities.add_entities([_mention(tenant, "d2", MNET_FULL)])
    kg = InMemoryKnowledgeGraphRepository()
    feature = EntityGraphFeature(entities, kg)
    feature.process(tenant, "d1")
    feature.process(tenant, "d2")

    assert resolve_tenant_aliases(kg, tenant) == 1
    assert resolve_tenant_aliases(kg, tenant) == 0  # second run is a no-op
    assert kg.entity_count(tenant) == 1


def test_fold_repoints_edges_and_merges_duplicates_with_combined_evidence() -> None:
    tenant = "t1"
    kg = InMemoryKnowledgeGraphRepository()
    # Nodes: X (subject), the alias M-net, and its canonical full name.
    x_id = canonical_entity_id(tenant, "PERSON", "Max Mustermann")
    alias_id = canonical_entity_id(tenant, "ORG", MNET)
    canon_id = canonical_entity_id(tenant, "ORG", MNET_FULL)
    kg.upsert_entities(
        [
            _kg_entity(tenant, x_id, EntityType.PERSON, "Max Mustermann"),
            _kg_entity(tenant, alias_id, EntityType.ORG, MNET),
            _kg_entity(tenant, canon_id, EntityType.ORG, MNET_FULL),
        ]
    )
    # Doc da: X -works_at-> alias (M-net). Doc db: X -works_at-> canonical (full name).
    _add_edge(kg, tenant, "da", x_id, "works_at", alias_id, "Max works at M-net.")
    _add_edge(
        kg, tenant, "db", x_id, "works_at", canon_id, "Max works at M-net Telekommunikations."
    )
    assert kg.edge_count(tenant) == 2

    resolve_tenant_aliases(kg, tenant)

    # The two edges collapse into one X -works_at-> canonical edge, evidence from BOTH documents.
    survivor_id = canonical_edge_id(tenant, x_id, "works_at", canon_id)
    edges = kg.edges_for_entity(tenant, canon_id)
    assert kg.edge_count(tenant) == 1
    assert len(edges) == 1
    assert edges[0].id == survivor_id
    assert edges[0].src_entity_id == x_id and edges[0].dst_entity_id == canon_id
    assert edges[0].evidence_count == 2  # combined provenance
    assert kg.edges_for_entity(tenant, alias_id) == []  # nothing references the folded node


def test_tenant_isolation() -> None:
    entities = InMemoryEntityRepository()
    for tenant in ("t-a", "t-b"):
        entities.add_entities([_mention(tenant, f"{tenant}-d1", MNET)])
        entities.add_entities([_mention(tenant, f"{tenant}-d2", MNET_FULL)])
    kg = InMemoryKnowledgeGraphRepository()
    feature = EntityGraphFeature(entities, kg)
    for tenant in ("t-a", "t-b"):
        feature.process(tenant, f"{tenant}-d1")
        feature.process(tenant, f"{tenant}-d2")

    # Fold only tenant A; tenant B is untouched.
    assert resolve_tenant_aliases(kg, "t-a") == 1
    assert kg.entity_count("t-a") == 1
    assert kg.entity_count("t-b") == 2
    assert kg.alias_map("t-b") == {}


def _kg_entity(tenant: str, node_id: str, entity_type: EntityType, value: str) -> KgEntity:
    return KgEntity(id=node_id, tenant_id=tenant, entity_type=entity_type, normalized_value=value)


def _add_edge(
    kg: InMemoryKnowledgeGraphRepository,
    tenant: str,
    document_id: str,
    src: str,
    predicate: str,
    dst: str,
    evidence: str,
) -> None:
    edge_id = canonical_edge_id(tenant, src, predicate, dst)
    edge = KgEdge(
        id=edge_id, tenant_id=tenant, src_entity_id=src, predicate=predicate, dst_entity_id=dst
    )
    prov = KgEdgeProvenance(
        id=uuid.uuid4().hex,
        tenant_id=tenant,
        edge_id=edge_id,
        document_id=document_id,
        evidence=evidence,
    )
    kg.replace_edges_for_document(tenant, document_id, [edge], [prov])
