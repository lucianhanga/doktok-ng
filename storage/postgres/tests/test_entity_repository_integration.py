"""Integration tests for the Postgres entity repository (test* tenants only)."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import Document, DocumentEntity, DocumentStatus, EntityType
from doktok_storage_postgres import Database, PostgresDocumentRepository, PostgresEntityRepository

TENANT = "test-a"


def _document(doc_id: str) -> Document:
    return Document(
        id=doc_id,
        tenant_id=TENANT,
        sha256="a" * 64,
        original_filename=f"{doc_id}.txt",
        status=DocumentStatus.ACTIVE,
        created_at=datetime.now(UTC),
    )


def _entity(eid: str, doc_id: str, etype: EntityType, value: str, freq: int) -> DocumentEntity:
    return DocumentEntity(
        id=eid,
        tenant_id=TENANT,
        document_id=doc_id,
        version_id="",
        entity_text=value,
        entity_type=etype,
        normalized_value=value,
        frequency=freq,
    )


def test_list_distinct_and_documents_for_entity(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    docs.add(_document("d1"))
    docs.add(_document("d2"))

    repo = PostgresEntityRepository(db)
    repo.add_entities(
        [
            _entity("e1", "d1", EntityType.EMAIL, "a@b.com", 2),
            _entity("e2", "d2", EntityType.EMAIL, "a@b.com", 1),
            _entity("e3", "d1", EntityType.MONEY, "$50", 1),
        ]
    )

    summaries = repo.list_distinct(TENANT)
    email = next(s for s in summaries if s.entity_type is EntityType.EMAIL)
    assert email.normalized_value == "a@b.com"
    assert email.document_count == 2
    assert email.occurrences == 3

    only_money = repo.list_distinct(TENANT, entity_type=EntityType.MONEY)
    assert [s.normalized_value for s in only_money] == ["$50"]

    docs_with_email = repo.documents_for_entity(TENANT, EntityType.EMAIL, "a@b.com")
    assert {d.id for d in docs_with_email} == {"d1", "d2"}
