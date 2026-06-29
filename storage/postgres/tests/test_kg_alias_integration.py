"""Integration tests for the KAG alias-folding tier against a real database.

The ``db`` fixture skips when no database is reachable and only touches ``test*`` tenants.
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
    KgEntity,
)
from doktok_core.features.processors import EntityGraphFeature
from doktok_core.knowledge_graph.alias import resolve_tenant_aliases
from doktok_core.knowledge_graph.predicates import canonical_edge_id
from doktok_core.knowledge_graph.resolve import canonical_entity_id
from doktok_storage_postgres import (
    Database,
    PostgresDocumentRepository,
    PostgresEntityRepository,
    PostgresKnowledgeGraphRepository,
)

TENANT = "test-alias"
TENANT_B = "test-alias-b"
MNET = "M-net"
MNET_FULL = "M-net Telekommunikations GmbH"


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


def _add_org_mention(db: Database, tenant: str, document_id: str, value: str) -> None:
    PostgresEntityRepository(db).add_entities(
        [
            DocumentEntity(
                id=uuid.uuid4().hex,
                tenant_id=tenant,
                document_id=document_id,
                version_id="",
                entity_text=value,
                entity_type=EntityType.ORG,
                normalized_value=value,
            )
        ]
    )


def test_alias_pass_merges_and_is_stable_across_reprocess(db: Database) -> None:
    _add_document(db, TENANT, "d1")
    _add_document(db, TENANT, "d2")
    _add_org_mention(db, TENANT, "d1", MNET)
    _add_org_mention(db, TENANT, "d2", MNET_FULL)

    kg = PostgresKnowledgeGraphRepository(db)
    feature = EntityGraphFeature(PostgresEntityRepository(db), kg)
    feature.process(TENANT, "d1")
    feature.process(TENANT, "d2")
    assert kg.entity_count(TENANT) == 2

    assert resolve_tenant_aliases(kg, TENANT) == 1
    assert kg.entity_count(TENANT) == 1
    canonical = canonical_entity_id(TENANT, "ORG", MNET_FULL)
    assert kg.get_entity(TENANT, canonical) is not None
    assert kg.get_entity(TENANT, canonical_entity_id(TENANT, "ORG", MNET)) is None
    assert {m.document_id for m in kg.mentions_for_entity(TENANT, canonical)} == {"d1", "d2"}

    # Stability: reprocessing the alias document keeps it pointed at the canonical node.
    feature.process(TENANT, "d1")
    assert kg.entity_count(TENANT) == 1
    d1_mentions = kg.mentions_for_document(TENANT, "d1")
    assert len(d1_mentions) == 1
    assert d1_mentions[0].canonical_entity_id == canonical

    # Idempotent: a second pass is a no-op.
    assert resolve_tenant_aliases(kg, TENANT) == 0
    assert kg.entity_count(TENANT) == 1


def test_fold_repoints_and_merges_edges(db: Database) -> None:
    kg = PostgresKnowledgeGraphRepository(db)
    x_id = canonical_entity_id(TENANT, "PERSON", "Max Mustermann")
    alias_id = canonical_entity_id(TENANT, "ORG", MNET)
    canon_id = canonical_entity_id(TENANT, "ORG", MNET_FULL)
    kg.upsert_entities(
        [
            KgEntity(
                id=x_id,
                tenant_id=TENANT,
                entity_type=EntityType.PERSON,
                normalized_value="Max Mustermann",
            ),
            KgEntity(
                id=alias_id, tenant_id=TENANT, entity_type=EntityType.ORG, normalized_value=MNET
            ),
            KgEntity(
                id=canon_id,
                tenant_id=TENANT,
                entity_type=EntityType.ORG,
                normalized_value=MNET_FULL,
            ),
        ]
    )
    _add_edge(kg, TENANT, "da", x_id, "works_at", alias_id, "Max works at M-net.")
    _add_edge(kg, TENANT, "db", x_id, "works_at", canon_id, "Max at M-net Telekommunikations.")
    assert kg.edge_count(TENANT) == 2

    resolve_tenant_aliases(kg, TENANT)

    survivor_id = canonical_edge_id(TENANT, x_id, "works_at", canon_id)
    edges = kg.edges_for_entity(TENANT, canon_id)
    assert kg.edge_count(TENANT) == 1
    assert len(edges) == 1
    assert edges[0].id == survivor_id
    assert edges[0].evidence_count == 2  # provenance from both documents combined
    assert kg.edges_for_entity(TENANT, alias_id) == []


def test_tenant_isolation(db: Database) -> None:
    for tenant in (TENANT, TENANT_B):
        _add_document(db, tenant, f"{tenant}-d1")
        _add_document(db, tenant, f"{tenant}-d2")
        _add_org_mention(db, tenant, f"{tenant}-d1", MNET)
        _add_org_mention(db, tenant, f"{tenant}-d2", MNET_FULL)
    kg = PostgresKnowledgeGraphRepository(db)
    feature = EntityGraphFeature(PostgresEntityRepository(db), kg)
    for tenant in (TENANT, TENANT_B):
        feature.process(tenant, f"{tenant}-d1")
        feature.process(tenant, f"{tenant}-d2")

    assert resolve_tenant_aliases(kg, TENANT) == 1
    assert kg.entity_count(TENANT) == 1
    assert kg.entity_count(TENANT_B) == 2  # tenant B untouched
    assert kg.alias_map(TENANT_B) == {}


def _add_edge(
    kg: PostgresKnowledgeGraphRepository,
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
