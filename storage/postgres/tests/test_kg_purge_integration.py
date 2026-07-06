"""Integration tests for PostgresKnowledgeGraphRepository.purge_document (issue #480): pruning
orphaned canonical nodes on document delete and the kg_edges FK cascade. Runs against a real
database; the ``db`` fixture skips automatically when none is reachable and only touches ``test*``
tenants. The "shared entity survives" case (which needs seeded document_entities mentions) is
covered by the in-memory test core/tests/test_kg_purge_document.py.
"""

from __future__ import annotations

from doktok_contracts.schemas import EntityType, KgEdge, KgEdgeProvenance, KgEntity
from doktok_core.knowledge_graph.predicates import canonical_edge_id
from doktok_core.knowledge_graph.resolve import canonical_entity_id
from doktok_storage_postgres import Database, PostgresKnowledgeGraphRepository

TENANT = "test-kg-purge"
TENANT_B = "test-kg-purge-b"


def _eid(tenant: str, entity_type: EntityType, value: str) -> str:
    return canonical_entity_id(tenant, entity_type.value, value)


def _node(tenant: str, entity_type: EntityType, value: str) -> KgEntity:
    return KgEntity(
        id=_eid(tenant, entity_type, value),
        tenant_id=tenant,
        entity_type=entity_type,
        normalized_value=value,
    )


def _seed(db: Database, tenant: str = TENANT) -> PostgresKnowledgeGraphRepository:
    """alice -EMPLOYED_BY-> acme, edge sourced from doc1. No mentions seeded, so both nodes are
    orphans (the SQL NOT EXISTS path) - the point is to exercise the delete + FK cascade."""
    kg = PostgresKnowledgeGraphRepository(db)
    alice = _node(tenant, EntityType.PERSON, "alice")
    acme = _node(tenant, EntityType.ORG, "acme corp")
    kg.upsert_entities([alice, acme])
    edge = KgEdge(
        id=canonical_edge_id(tenant, alice.id, "EMPLOYED_BY", acme.id),
        tenant_id=tenant,
        src_entity_id=alice.id,
        predicate="EMPLOYED_BY",
        dst_entity_id=acme.id,
    )
    kg.replace_edges_for_document(
        tenant,
        "doc1",
        [edge],
        [
            KgEdgeProvenance(
                id=f"p:{edge.id}",
                tenant_id=tenant,
                edge_id=edge.id,
                document_id="doc1",
                evidence="x",
            )
        ],
    )
    return kg


def test_purge_prunes_orphan_nodes_and_cascades_edges(db: Database) -> None:
    kg = _seed(db)
    assert kg.entity_count(TENANT) == 2
    assert kg.edge_count(TENANT) == 1

    pruned = kg.purge_document(TENANT, "doc1")

    assert pruned == 2  # both nodes have no mentions -> orphaned
    assert kg.entity_count(TENANT) == 0
    assert kg.edge_count(TENANT) == 0  # kg_edges cascade when their endpoint node is deleted


def test_purge_is_idempotent(db: Database) -> None:
    kg = _seed(db)
    assert kg.purge_document(TENANT, "doc1") == 2
    assert kg.purge_document(TENANT, "doc1") == 0


def test_purge_is_tenant_scoped(db: Database) -> None:
    kg = _seed(db)
    _seed(db, TENANT_B)
    kg.purge_document(TENANT, "doc1")
    assert kg.entity_count(TENANT) == 0
    assert kg.entity_count(TENANT_B) == 2  # the other tenant is untouched
