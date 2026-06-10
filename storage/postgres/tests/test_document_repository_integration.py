"""Integration tests for the Postgres document repository.

Uses only ``test*`` tenants; cleanup is scoped in conftest. Skipped without a database.
"""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import Document, DocumentStatus
from doktok_storage_postgres import Database, PostgresDocumentRepository

# Tenant ids start with "test" so conftest cleanup matches them (and nothing else).
TEST_TENANT_A = "test-a"
TEST_TENANT_B = "test-b"


def _doc(doc_id: str, tenant: str) -> Document:
    return Document(
        id=doc_id,
        tenant_id=tenant,
        sha256="a" * 64,
        original_filename=f"{doc_id}.txt",
        detected_mime="text/plain",
        title=doc_id,
        status=DocumentStatus.ACTIVE,
        storage_path=f"/docs.active/{doc_id}",
        created_at=datetime.now(UTC),
        activated_at=datetime.now(UTC),
        metadata={"page_count": 1},
    )


def test_add_get_and_tenant_isolation(db: Database) -> None:
    repo = PostgresDocumentRepository(db)
    repo.add(_doc("a-doc", TEST_TENANT_A))
    repo.add(_doc("b-doc", TEST_TENANT_B))

    fetched = repo.get(TEST_TENANT_A, "a-doc")
    assert fetched is not None
    assert fetched.status is DocumentStatus.ACTIVE
    assert fetched.metadata == {"page_count": 1}

    assert [d.id for d in repo.list_documents(TEST_TENANT_A)] == ["a-doc"]
    assert repo.get(TEST_TENANT_A, "b-doc") is None
