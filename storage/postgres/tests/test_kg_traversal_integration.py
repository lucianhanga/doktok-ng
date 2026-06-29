"""Integration tests for the Postgres knowledge-graph traversal (KAG Phase 3): bounded k-hop
neighborhood + shortest A<->B path via recursive CTEs. Runs against a real database; the ``db``
fixture skips automatically when none is reachable and only ever touches ``test*`` tenants.
"""

from __future__ import annotations

from doktok_contracts.schemas import EntityType, KgEdge, KgEdgeProvenance, KgEntity
from doktok_core.knowledge_graph.predicates import canonical_edge_id
from doktok_core.knowledge_graph.resolve import canonical_entity_id
from doktok_storage_postgres import Database, PostgresKnowledgeGraphRepository

TENANT = "test-kgt"
TENANT_B = "test-kgt-b"


def _eid(tenant: str, entity_type: EntityType, value: str) -> str:
    return canonical_entity_id(tenant, entity_type.value, value)


def _node(tenant: str, entity_type: EntityType, value: str) -> KgEntity:
    return KgEntity(
        id=_eid(tenant, entity_type, value),
        tenant_id=tenant,
        entity_type=entity_type,
        normalized_value=value,
    )


def _edge(tenant: str, src: str, predicate: str, dst: str) -> KgEdge:
    return KgEdge(
        id=canonical_edge_id(tenant, src, predicate, dst),
        tenant_id=tenant,
        src_entity_id=src,
        predicate=predicate,
        dst_entity_id=dst,
    )


def _prov(tenant: str, edge_id: str, doc: str, chunk: str) -> KgEdgeProvenance:
    return KgEdgeProvenance(
        id=f"p:{edge_id}:{doc}",
        tenant_id=tenant,
        edge_id=edge_id,
        document_id=doc,
        chunk_id=chunk,
        evidence=f"evidence for {edge_id[:8]}",
    )


def _seed(db: Database, tenant: str = TENANT) -> PostgresKnowledgeGraphRepository:
    """alice -EMPLOYED_BY-> acme (doc1); acme -LOCATED_IN-> hamburg (doc2)."""
    kg = PostgresKnowledgeGraphRepository(db)
    alice = _node(tenant, EntityType.PERSON, "alice")
    acme = _node(tenant, EntityType.ORG, "acme corp")
    hamburg = _node(tenant, EntityType.GPE, "hamburg")
    kg.upsert_entities([alice, acme, hamburg])
    e1 = _edge(tenant, alice.id, "EMPLOYED_BY", acme.id)
    e2 = _edge(tenant, acme.id, "LOCATED_IN", hamburg.id)
    kg.replace_edges_for_document(tenant, "doc1", [e1], [_prov(tenant, e1.id, "doc1", "c1")])
    kg.replace_edges_for_document(tenant, "doc2", [e2], [_prov(tenant, e2.id, "doc2", "c2")])
    return kg


def test_neighborhood_one_hop_bounded(db: Database) -> None:
    kg = _seed(db)
    edges, prov = kg.neighborhood(TENANT, [_eid(TENANT, EntityType.PERSON, "alice")], hops=1)
    assert len(edges) == 1
    assert edges[0].predicate == "EMPLOYED_BY"
    assert {p.document_id for p in prov} == {"doc1"}


def test_neighborhood_two_hops_reaches_further(db: Database) -> None:
    kg = _seed(db)
    edges, _prov = kg.neighborhood(TENANT, [_eid(TENANT, EntityType.PERSON, "alice")], hops=2)
    assert len(edges) == 2


def test_neighborhood_respects_edge_limit(db: Database) -> None:
    kg = _seed(db)
    edges, _prov = kg.neighborhood(
        TENANT, [_eid(TENANT, EntityType.ORG, "acme corp")], hops=1, edge_limit=1
    )
    assert len(edges) == 1


def test_path_between_two_entities(db: Database) -> None:
    kg = _seed(db)
    edges, prov = kg.path_between(
        TENANT,
        _eid(TENANT, EntityType.PERSON, "alice"),
        _eid(TENANT, EntityType.GPE, "hamburg"),
        max_hops=2,
    )
    assert {e.predicate for e in edges} == {"EMPLOYED_BY", "LOCATED_IN"}
    assert {p.document_id for p in prov} == {"doc1", "doc2"}


def test_path_between_empty_beyond_hop_bound(db: Database) -> None:
    kg = _seed(db)
    edges, prov = kg.path_between(
        TENANT,
        _eid(TENANT, EntityType.PERSON, "alice"),
        _eid(TENANT, EntityType.GPE, "hamburg"),
        max_hops=1,
    )
    assert edges == [] and prov == []


def test_traversal_tenant_isolation(db: Database) -> None:
    _seed(db, TENANT)
    _seed(db, TENANT_B)
    # Tenant B's traversal only ever sees tenant B's edges.
    edges, _prov = PostgresKnowledgeGraphRepository(db).neighborhood(
        TENANT_B, [_eid(TENANT_B, EntityType.PERSON, "alice")], hops=2
    )
    assert all(e.tenant_id == TENANT_B for e in edges)
    assert len(edges) == 2
