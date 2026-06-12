"""Integration tests for the Postgres document repository.

Uses only ``test*`` tenants; cleanup is scoped in conftest. Skipped without a database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from doktok_contracts.schemas import Document, DocumentStatus
from doktok_storage_postgres import (
    Database,
    PostgresDocumentRepository,
    PostgresFeatureRepository,
)

# Tenant ids start with "test" so conftest cleanup matches them (and nothing else).
TEST_TENANT_A = "test-a"
TEST_TENANT_B = "test-b"
TEST_TENANT_PAGE = "test-page"


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

    items, total, next_anchor = repo.list_documents(TEST_TENANT_A)
    assert [d.id for d in items] == ["a-doc"] and total == 1 and next_anchor is None
    assert repo.get(TEST_TENANT_A, "b-doc") is None


def _page_doc(doc_id: str, *, when: datetime) -> Document:
    return Document(
        id=doc_id,
        tenant_id=TEST_TENANT_PAGE,
        sha256=(doc_id + "a" * 64)[:64],  # distinct (active-sha unique index)
        original_filename=f"{doc_id}.txt",
        status=DocumentStatus.ACTIVE,
        created_at=when,
    )


def test_keyset_pagination_orders_and_pages_without_overlap(db: Database) -> None:
    repo = PostgresDocumentRepository(db)
    base = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(3):
        repo.add(_page_doc(f"p{i}", when=base + timedelta(minutes=i)))

    page1, total, anchor = repo.list_documents(TEST_TENANT_PAGE, limit=2)
    assert [d.id for d in page1] == ["p2", "p1"]  # newest first
    assert total == 3 and anchor is not None

    page2, _, anchor2 = repo.list_documents(TEST_TENANT_PAGE, limit=2, cursor=anchor)
    assert [d.id for d in page2] == ["p0"]  # no overlap with page 1
    assert anchor2 is None  # last page


def test_needs_attention_filter_uses_feature_ledger(db: Database) -> None:
    repo = PostgresDocumentRepository(db)
    feats = PostgresFeatureRepository(db)
    base = datetime(2024, 2, 1, tzinfo=UTC)
    for i in range(2):
        repo.add(_page_doc(f"a{i}", when=base + timedelta(minutes=i)))
    feats.ensure_for_active(TEST_TENANT_PAGE, [("doc_metadata", 1)])  # pending for a0, a1
    feats.record_done(TEST_TENANT_PAGE, "a0", "doc_metadata", 1)  # a0 now done; a1 still pending

    items, total, _ = repo.list_documents(TEST_TENANT_PAGE, needs_attention=True)
    ids = {d.id for d in items}
    assert "a1" in ids and "a0" not in ids and total == len(ids)
