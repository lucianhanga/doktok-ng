"""Integration tests for relations_for_document - a document's knowledge-graph footprint (#731)."""

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

TENANT = "test-kg-rel"
TENANT_B = "test-kg-rel-b"


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


def _build_kg(db: Database, tenant: str, doc_ids: list[str]) -> PostgresKnowledgeGraphRepository:
    kg = PostgresKnowledgeGraphRepository(db)
    feature = EntityGraphFeature(PostgresEntityRepository(db), kg)
    for doc_id in doc_ids:
        feature.process(tenant, doc_id)
    return kg


def test_relations_maps_mentions_to_canonical_nodes(db: Database) -> None:
    _add_document(db, TENANT, "rel-d1")
    _add_document(db, TENANT, "rel-d2")
    _add_mention(db, TENANT, "rel-d1", EntityType.PERSON, "Ada Lovelace")
    _add_mention(db, TENANT, "rel-d1", EntityType.ORG, "Analytical Engine Co")
    _add_mention(db, TENANT, "rel-d2", EntityType.PERSON, "Ada Lovelace")
    kg = _build_kg(db, TENANT, ["rel-d1", "rel-d2"])

    relations = kg.relations_for_document(TENANT, "rel-d1")
    by_value = {e.mention_value: e for e in relations.entities}
    assert by_value["Ada Lovelace"].entity_type == "PERSON"
    assert by_value["Ada Lovelace"].node_label == "Ada Lovelace"
    assert by_value["Analytical Engine Co"].node_id
    assert relations.relations == []  # no edges seeded yet
    # Tenant isolation: another tenant sees nothing of this document.
    assert kg.relations_for_document(TENANT_B, "rel-d1").entities == []


def test_relations_returns_edges_touching_the_documents_nodes(db: Database) -> None:
    _add_document(db, TENANT, "rel-d3")
    _add_document(db, TENANT, "rel-d4")
    _add_mention(db, TENANT, "rel-d3", EntityType.PERSON, "Ada Lovelace")
    _add_mention(db, TENANT, "rel-d3", EntityType.ORG, "Analytical Engine Co")
    _add_mention(db, TENANT, "rel-d4", EntityType.GPE, "Hamburg")
    kg = _build_kg(db, TENANT, ["rel-d3", "rel-d4"])
    person = canonical_entity_id(TENANT, EntityType.PERSON.value, "Ada Lovelace")
    org = canonical_entity_id(TENANT, EntityType.ORG.value, "Analytical Engine Co")
    edge = KgEdge(
        id=canonical_edge_id(TENANT, person, "WORKS_FOR", org),
        tenant_id=TENANT,
        src_entity_id=person,
        predicate="WORKS_FOR",
        dst_entity_id=org,
    )
    kg.add_edges(
        [edge],
        [
            KgEdgeProvenance(
                id=uuid.uuid4().hex,
                tenant_id=TENANT,
                edge_id=edge.id,
                document_id="rel-d3",
                chunk_id=None,
                evidence="Ada Lovelace works for Analytical Engine Co.",
            )
        ],
    )

    relations = kg.relations_for_document(TENANT, "rel-d3")
    assert len(relations.relations) == 1
    triple = relations.relations[0]
    assert (triple.subject, triple.predicate, triple.object) == (
        "Ada Lovelace",
        "WORKS_FOR",
        "Analytical Engine Co",
    )
    assert triple.evidence_count == 1
    # A document whose nodes share no endpoint with the edge does not see it.
    other = kg.relations_for_document(TENANT, "rel-d4")
    assert [e.mention_value for e in other.entities] == ["Hamburg"]
    assert other.relations == []
