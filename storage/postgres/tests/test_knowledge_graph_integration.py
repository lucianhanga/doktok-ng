"""Integration tests for the Postgres knowledge-graph repository + the EntityGraphFeature (KAG P1).

Runs against a real database; the ``db`` fixture skips automatically when none is reachable and only
ever touches ``test*`` tenants.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from doktok_contracts.schemas import Document, DocumentEntity, DocumentStatus, EntityType
from doktok_core.features.processors import EntityGraphFeature
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
