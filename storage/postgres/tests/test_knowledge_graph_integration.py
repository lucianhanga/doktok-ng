"""Integration tests for the Postgres knowledge-graph repository + EntityGraphFeature (KAG P1+P2).

Runs against a real database; the ``db`` fixture skips automatically when none is reachable and only
ever touches ``test*`` tenants.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from doktok_contracts.schemas import (
    Document,
    DocumentEntity,
    DocumentStatus,
    EntityType,
    KgEdge,
    KgEdgeProvenance,
)
from doktok_core.features.processors import EntityGraphFeature
from doktok_core.knowledge_graph.predicates import canonical_edge_id
from doktok_core.knowledge_graph.resolve import canonical_entity_id
from doktok_storage_postgres import (
    Database,
    PostgresDocumentRepository,
    PostgresEntityRepository,
    PostgresKnowledgeGraphRepository,
)

TENANT = "test-kg"
TENANT_B = "test-kg-b"


def _add_document(db: Database, tenant: str, document_id: str) -> None:
    PostgresDocumentRepository(db).add(
        Document(
            id=document_id,
            tenant_id=tenant,
            sha256=(document_id + "a" * 64)[:64],
            original_filename=f"{document_id}.pdf",
            status=DocumentStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
    )


def _add_mention(
    db: Database, tenant: str, document_id: str, entity_type: EntityType, value: str
) -> None:
    PostgresEntityRepository(db).add_entities(
        [
            DocumentEntity(
                id=uuid.uuid4().hex,
                tenant_id=tenant,
                document_id=document_id,
                version_id="",
                entity_text=value,
                entity_type=entity_type,
                normalized_value=value,
            )
        ]
    )


def test_cross_document_merge_and_idempotent_reprocess(db: Database) -> None:
    _add_document(db, TENANT, "d1")
    _add_document(db, TENANT, "d2")
    _add_mention(db, TENANT, "d1", EntityType.PERSON, "Ada Lovelace")
    _add_mention(db, TENANT, "d2", EntityType.PERSON, "Ada Lovelace")
    _add_mention(db, TENANT, "d2", EntityType.ORG, "Analytical Engine Co")

    kg = PostgresKnowledgeGraphRepository(db)
    feature = EntityGraphFeature(PostgresEntityRepository(db), kg)
    feature.process(TENANT, "d1")
    feature.process(TENANT, "d2")

    assert kg.entity_count(TENANT) == 2  # one shared PERSON + one ORG
    person = canonical_entity_id(TENANT, EntityType.PERSON.value, "Ada Lovelace")
    node = kg.get_entity(TENANT, person)
    assert node is not None and node.normalized_value == "Ada Lovelace"
    assert {m.document_id for m in kg.mentions_for_entity(TENANT, person)} == {"d1", "d2"}

    # Re-running both documents must not duplicate nodes or mentions.
    feature.process(TENANT, "d1")
    feature.process(TENANT, "d2")
    assert kg.entity_count(TENANT) == 2
    assert len(kg.mentions_for_entity(TENANT, person)) == 2


def test_tenant_isolation(db: Database) -> None:
    _add_document(db, TENANT, "d1")
    _add_document(db, TENANT_B, "d2")
    _add_mention(db, TENANT, "d1", EntityType.PERSON, "Grace Hopper")
    _add_mention(db, TENANT_B, "d2", EntityType.PERSON, "Grace Hopper")

    kg = PostgresKnowledgeGraphRepository(db)
    feature = EntityGraphFeature(PostgresEntityRepository(db), kg)
    feature.process(TENANT, "d1")
    feature.process(TENANT_B, "d2")

    a_id = canonical_entity_id(TENANT, EntityType.PERSON.value, "Grace Hopper")
    b_id = canonical_entity_id(TENANT_B, EntityType.PERSON.value, "Grace Hopper")
    assert a_id != b_id
    assert kg.get_entity(TENANT, b_id) is None  # cannot read tenant B's node as tenant A
    assert kg.get_entity(TENANT_B, a_id) is None
    assert kg.entity_count(TENANT) == 1
    assert kg.entity_count(TENANT_B) == 1


def test_document_delete_cascades_mentions(db: Database) -> None:
    _add_document(db, TENANT, "d1")
    _add_mention(db, TENANT, "d1", EntityType.PERSON, "Alan Turing")

    kg = PostgresKnowledgeGraphRepository(db)
    feature = EntityGraphFeature(PostgresEntityRepository(db), kg)
    feature.process(TENANT, "d1")
    assert len(kg.mentions_for_document(TENANT, "d1")) == 1

    # Deleting the document cascades documents -> document_entities -> kg_entity_mentions.
    PostgresDocumentRepository(db).delete(TENANT, "d1")
    assert kg.mentions_for_document(TENANT, "d1") == []


# =============================== Phase 2: edges ================================


def _make_edge(tenant: str, src_id: str, dst_id: str, predicate: str = "EMPLOYED_BY") -> KgEdge:
    edge_id = canonical_edge_id(tenant, src_id, predicate, dst_id)
    return KgEdge(
        id=edge_id,
        tenant_id=tenant,
        src_entity_id=src_id,
        predicate=predicate,
        dst_entity_id=dst_id,
    )


def _make_prov(
    tenant: str, edge_id: str, doc_id: str, evidence: str = "some text"
) -> KgEdgeProvenance:
    return KgEdgeProvenance(
        id=uuid.uuid4().hex,
        tenant_id=tenant,
        edge_id=edge_id,
        document_id=doc_id,
        evidence=evidence,
    )


def _ensure_entity_nodes(db: Database, tenant: str, doc_id: str) -> tuple[str, str]:
    """Insert PERSON 'p1' + ORG 'o1' nodes via EntityGraphFeature; return (person_id, org_id)."""
    _add_mention(db, tenant, doc_id, EntityType.PERSON, "p1")
    _add_mention(db, tenant, doc_id, EntityType.ORG, "o1")
    kg = PostgresKnowledgeGraphRepository(db)
    EntityGraphFeature(PostgresEntityRepository(db), kg).process(tenant, doc_id)
    return (
        canonical_entity_id(tenant, EntityType.PERSON.value, "p1"),
        canonical_entity_id(tenant, EntityType.ORG.value, "o1"),
    )


def test_edge_replace_and_idempotent(db: Database) -> None:
    """Insert edges for a document; re-running produces the same edge count."""
    _add_document(db, TENANT, "d1")
    person_id, org_id = _ensure_entity_nodes(db, TENANT, "d1")

    kg = PostgresKnowledgeGraphRepository(db)
    edge = _make_edge(TENANT, person_id, org_id)
    prov = _make_prov(TENANT, edge.id, "d1")

    kg.replace_edges_for_document(TENANT, "d1", [edge], [prov])
    assert kg.edge_count(TENANT) == 1

    # Re-run: idempotent
    kg.replace_edges_for_document(TENANT, "d1", [edge], [_make_prov(TENANT, edge.id, "d1")])
    assert kg.edge_count(TENANT) == 1


def test_edge_evidence_count_tracks_provenance(db: Database) -> None:
    """Two documents contributing the same edge -> evidence_count=2; remove one -> count=1."""
    _add_document(db, TENANT, "d1")
    _add_document(db, TENANT, "d2")
    person_id, org_id = _ensure_entity_nodes(db, TENANT, "d1")
    # d2 also needs the entity rows (EntityGraphFeature only processes one doc at a time)
    _add_mention(db, TENANT, "d2", EntityType.PERSON, "p1")
    _add_mention(db, TENANT, "d2", EntityType.ORG, "o1")
    kg_repo = PostgresKnowledgeGraphRepository(db)
    EntityGraphFeature(PostgresEntityRepository(db), kg_repo).process(TENANT, "d2")

    kg = PostgresKnowledgeGraphRepository(db)
    edge = _make_edge(TENANT, person_id, org_id)
    prov1 = _make_prov(TENANT, edge.id, "d1", "doc 1 evidence")
    prov2 = _make_prov(TENANT, edge.id, "d2", "doc 2 evidence")

    kg.replace_edges_for_document(TENANT, "d1", [edge], [prov1])
    kg.replace_edges_for_document(TENANT, "d2", [edge], [prov2])

    edges = kg.edges_for_entity(TENANT, person_id)
    assert len(edges) == 1
    assert edges[0].evidence_count == 2

    # Remove d2's contribution
    kg.replace_edges_for_document(TENANT, "d2", [], [])
    edges = kg.edges_for_entity(TENANT, person_id)
    assert len(edges) == 1
    assert edges[0].evidence_count == 1


def test_edge_prune_on_zero_evidence(db: Database) -> None:
    """After removing all provenance, the edge row is deleted."""
    _add_document(db, TENANT, "d1")
    person_id, org_id = _ensure_entity_nodes(db, TENANT, "d1")

    kg = PostgresKnowledgeGraphRepository(db)
    edge = _make_edge(TENANT, person_id, org_id)
    prov = _make_prov(TENANT, edge.id, "d1")

    kg.replace_edges_for_document(TENANT, "d1", [edge], [prov])
    assert kg.edge_count(TENANT) == 1

    # Remove the only provenance -> edge should be pruned
    kg.replace_edges_for_document(TENANT, "d1", [], [])
    assert kg.edge_count(TENANT) == 0


def test_edge_tenant_isolation(db: Database) -> None:
    """Edges from two tenants are separate and cannot cross-read."""
    _add_document(db, TENANT, "d1")
    _add_document(db, TENANT_B, "d2")
    person_a, org_a = _ensure_entity_nodes(db, TENANT, "d1")
    _add_mention(db, TENANT_B, "d2", EntityType.PERSON, "p1")
    _add_mention(db, TENANT_B, "d2", EntityType.ORG, "o1")
    kg_repo = PostgresKnowledgeGraphRepository(db)
    EntityGraphFeature(PostgresEntityRepository(db), kg_repo).process(TENANT_B, "d2")
    person_b = canonical_entity_id(TENANT_B, EntityType.PERSON.value, "p1")
    org_b = canonical_entity_id(TENANT_B, EntityType.ORG.value, "o1")

    kg = PostgresKnowledgeGraphRepository(db)
    edge_a = _make_edge(TENANT, person_a, org_a)
    edge_b = _make_edge(TENANT_B, person_b, org_b)

    kg.replace_edges_for_document(TENANT, "d1", [edge_a], [_make_prov(TENANT, edge_a.id, "d1")])
    kg.replace_edges_for_document(TENANT_B, "d2", [edge_b], [_make_prov(TENANT_B, edge_b.id, "d2")])

    assert kg.edge_count(TENANT) == 1
    assert kg.edge_count(TENANT_B) == 1
    # Cannot see the other tenant's edges
    assert kg.edges_for_entity(TENANT, person_b) == []
    assert kg.edges_for_entity(TENANT_B, person_a) == []


def test_reject_merge_persists_and_is_idempotent(db: Database) -> None:
    """reject_merge stores a pair key; rejected_pair_keys reads it back, tenant-scoped (#530)."""
    kg = PostgresKnowledgeGraphRepository(db)
    assert kg.rejected_pair_keys(TENANT) == set()
    kg.reject_merge(TENANT, "pair-a")
    kg.reject_merge(TENANT, "pair-a")  # idempotent: no duplicate, no error
    kg.reject_merge(TENANT, "pair-b")
    kg.reject_merge(TENANT_B, "pair-c")  # other tenant, must not leak
    assert kg.rejected_pair_keys(TENANT) == {"pair-a", "pair-b"}
    assert kg.rejected_pair_keys(TENANT_B) == {"pair-c"}
