"""Integration tests for unified document metadata search and repeated filters."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import (
    Document,
    DocumentChunk,
    DocumentEntity,
    DocumentStatus,
    EntityType,
    TokenMatch,
)
from doktok_storage_postgres import (
    Database,
    PostgresCategoryRepository,
    PostgresChunkRepository,
    PostgresDocumentRepository,
    PostgresEntityRepository,
)

TEST_TENANT = "test-document-search"
OTHER_TENANT = "test-document-search-other"


def _doc(
    doc_id: str,
    *,
    tenant: str = TEST_TENANT,
    title: str = "Other",
    filename: str | None = None,
    status: DocumentStatus = DocumentStatus.ACTIVE,
) -> Document:
    return Document(
        id=doc_id,
        tenant_id=tenant,
        sha256=(doc_id + "a" * 64)[:64],
        original_filename=filename or f"{doc_id}.txt",
        title=title,
        status=status,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _entity(doc_id: str, value: str, *, tenant: str = TEST_TENANT) -> DocumentEntity:
    return DocumentEntity(
        id=f"{doc_id}-{value}",
        tenant_id=tenant,
        document_id=doc_id,
        version_id="v1",
        entity_text=value,
        entity_type=EntityType.CUSTOM_TOKEN,
        normalized_value=value,
    )


def test_unified_query_matches_title_filename_and_entities_only(db: Database) -> None:
    repo = PostgresDocumentRepository(db)
    entities = PostgresEntityRepository(db)
    for doc in (
        _doc("title", title="Northstar"),
        _doc("filename", filename="0000982.pdf"),
        _doc("entity"),
        _doc("fulltext"),
        _doc("other-entity", tenant=OTHER_TENANT),
    ):
        repo.add(doc)
    entities.add_entities(
        [
            _entity("title", "2026"),
            _entity("entity", "acme gmbh"),
            _entity("other-entity", "acme gmbh", tenant=OTHER_TENANT),
        ]
    )
    PostgresChunkRepository(db).add_chunks(
        [
            DocumentChunk(
                id="fulltext-chunk",
                tenant_id=TEST_TENANT,
                document_id="fulltext",
                version_id="v1",
                text="only-in-extracted-text",
            )
        ],
        [[0.1] * 1024],
    )

    items, total, _ = repo.list_documents(TEST_TENANT, q="northstar")
    assert {d.id for d in items} == {"title"} and total == 1
    items, _, _ = repo.list_documents(TEST_TENANT, q="0000982.pdf")
    assert {d.id for d in items} == {"filename"}
    items, _, _ = repo.list_documents(TEST_TENANT, q="ACME")
    assert {d.id for d in items} == {"entity"}
    items, _, _ = repo.list_documents(TEST_TENANT, q="northstar \t 2026")
    assert {d.id for d in items} == {"title"}
    items, total, _ = repo.list_documents(TEST_TENANT, q="northstar missing")
    assert items == [] and total == 0
    items, total, _ = repo.list_documents(TEST_TENANT, q="only-in-extracted-text")
    assert items == [] and total == 0

    ids, total, truncated = repo.list_document_ids(TEST_TENANT, q="northstar 2026")
    assert ids == ["title"] and total == 1 and truncated is False


def test_repeated_filters_compose_with_legacy_tokens_and_singular_calls(db: Database) -> None:
    repo = PostgresDocumentRepository(db)
    categories = PostgresCategoryRepository(db)
    entities = PostgresEntityRepository(db)
    for doc in (
        _doc("active", title="Northstar"),
        _doc("failed", title="Northstar", status=DocumentStatus.FAILED),
        _doc("processing", title="Northstar", status=DocumentStatus.PROCESSING),
        _doc("other", title="Other"),
    ):
        repo.add(doc)
    invoices = categories.create(TEST_TENANT, "Invoices", "invoices")
    contracts = categories.create(TEST_TENANT, "Contracts", "contracts")
    assert invoices is not None and contracts is not None
    categories.set_document_categories(TEST_TENANT, "active", [invoices.id])
    categories.set_document_categories(TEST_TENANT, "failed", [contracts.id])
    categories.set_document_categories(TEST_TENANT, "processing", [invoices.id])
    categories.set_document_categories(TEST_TENANT, "other", [invoices.id])
    entities.add_entities(
        [
            _entity("active", "Acme"),
            _entity("failed", "Bob"),
            _entity("processing", "Acme"),
            _entity("other", "Acme"),
        ]
    )

    items, total, _ = repo.list_documents(
        TEST_TENANT,
        q="northstar",
        status=(DocumentStatus.ACTIVE, DocumentStatus.FAILED),
        category=("Invoices", "Contracts"),
        tokens=("Acme", "Bob"),
        token_match=TokenMatch.ANY,
    )
    assert {d.id for d in items} == {"active", "failed"} and total == 2
    ids, total, truncated = repo.list_document_ids(
        TEST_TENANT,
        q="northstar",
        status=(DocumentStatus.ACTIVE, DocumentStatus.FAILED),
        category=("Invoices", "Contracts"),
        tokens=("Acme", "Bob"),
        token_match=TokenMatch.ANY,
    )
    assert ids == ["active", "failed"] and total == 2 and truncated is False

    items, _, _ = repo.list_documents(
        TEST_TENANT, status=DocumentStatus.ACTIVE, category="Invoices"
    )
    assert {d.id for d in items} == {"active", "other"}
