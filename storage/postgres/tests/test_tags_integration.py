"""Integration tests for the Postgres tags store (epic #543, #544; test* tenants)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from doktok_contracts.schemas import Document, DocumentStatus, Tag
from doktok_storage_postgres import (
    Database,
    PostgresDocumentRepository,
    PostgresTagRepository,
)

TENANT = "test-tags"
TENANT_B = "test-tags-b"


def _tag(tag_id: str, name: str, tenant: str = TENANT, **kw: Any) -> Tag:
    kw.setdefault("normalized", name.casefold())
    return Tag(id=tag_id, tenant_id=tenant, name=name, created_at=datetime.now(UTC), **kw)


def _doc(doc_id: str) -> Document:
    return Document(
        id=doc_id,
        tenant_id=TENANT,
        sha256=(doc_id + "a" * 64)[:64],
        original_filename=f"{doc_id}.pdf",
        status=DocumentStatus.ACTIVE,
        created_at=datetime.now(UTC),
    )


def test_tag_crud_and_tenant_isolation(db: Database) -> None:
    repo = PostgresTagRepository(db)
    repo.create_tag(_tag("t1", "Rome Trip"))
    fetched = repo.get_tag(TENANT, "t1")
    assert fetched is not None and fetched.name == "Rome Trip" and fetched.status == "active"
    assert repo.find_by_normalized(TENANT, "rome trip") is not None
    assert repo.get_tag(TENANT_B, "t1") is None  # tenant isolation
    assert repo.find_by_normalized(TENANT_B, "rome trip") is None

    updated = repo.update_tag(TENANT, "t1", name="Rome 2026", normalized="rome 2026", color="teal")
    assert updated is not None and updated.name == "Rome 2026" and updated.color == "teal"
    assert [t.name for t in repo.list_tags(TENANT)] == ["Rome 2026"]

    repo.set_tag_status(TENANT, "t1", "merged", merged_into="t2")
    assert repo.list_tags(TENANT) == []  # merged tags are not active
    assert [t.name for t in repo.list_tags(TENANT, status="merged")] == ["Rome 2026"]

    repo.delete_tag(TENANT, "t1")
    assert repo.get_tag(TENANT, "t1") is None


def test_normalized_is_unique_per_tenant(db: Database) -> None:
    repo = PostgresTagRepository(db)
    repo.create_tag(_tag("t1", "Rome Trip"))
    with pytest.raises(Exception):  # noqa: B017 - the DB unique constraint rejects the duplicate
        repo.create_tag(_tag("t2", "Rome Trip"))
    # The same normalized key is fine in ANOTHER tenant.
    repo.create_tag(_tag("t3", "Rome Trip", tenant=TENANT_B))


def test_find_similar_near_miss(db: Database) -> None:
    repo = PostgresTagRepository(db)
    repo.create_tag(_tag("t1", "Rome Trip", normalized="rome trip"))
    repo.create_tag(_tag("t2", "Electricity 2024", normalized="electricity 2024"))
    # A token-set re-order ("trip rome") is a near-miss of "rome trip".
    similar = repo.find_similar(TENANT, "trip rome")
    assert any(t.id == "t1" for t in similar)
    assert not any(t.id == "t2" for t in similar)


def test_link_round_trip_counts_and_document_cascade(db: Database) -> None:
    PostgresDocumentRepository(db).add(_doc("d1"))
    PostgresDocumentRepository(db).add(_doc("d2"))
    repo = PostgresTagRepository(db)
    repo.create_tag(_tag("t1", "Rome Trip"))
    repo.create_tag(_tag("t2", "Receipts"))
    repo.link(TENANT, "d1", "t1")
    repo.link(TENANT, "d1", "t2")
    repo.link(TENANT, "d2", "t1")

    assert [t.name for t in repo.list_for_document(TENANT, "d1")] == ["Receipts", "Rome Trip"]
    assert repo.count_for_documents(TENANT, ["d1", "d2", "d3"]) == {"d1": 2, "d2": 1}
    assert repo.document_count(TENANT, "t1") == 2
    assert repo.tag_counts(TENANT) == {"t1": 2, "t2": 1}

    repo.unlink(TENANT, "d1", "t2")
    assert [t.name for t in repo.list_for_document(TENANT, "d1")] == ["Rome Trip"]

    # Deleting the document cascades its links away.
    PostgresDocumentRepository(db).delete(TENANT, "d1")
    assert repo.document_count(TENANT, "t1") == 1
    assert repo.list_for_document(TENANT, "d1") == []


def test_document_tag_cap_trigger(db: Database) -> None:
    PostgresDocumentRepository(db).add(_doc("d-cap"))
    repo = PostgresTagRepository(db)
    for i in range(20):
        repo.create_tag(_tag(f"cap-{i}", f"tag {i}", normalized=f"tag {i}"))
        repo.link(TENANT, "d-cap", f"cap-{i}")
    repo.create_tag(_tag("cap-20", "tag 20", normalized="tag 20"))
    with pytest.raises(Exception):  # noqa: B017 - the 21st tag on one document hits the cap
        repo.link(TENANT, "d-cap", "cap-20")


def test_tags_for_documents_batched(db: Database) -> None:
    PostgresDocumentRepository(db).add(_doc("d1"))
    PostgresDocumentRepository(db).add(_doc("d2"))
    repo = PostgresTagRepository(db)
    repo.create_tag(_tag("t1", "Rome"))
    repo.create_tag(_tag("t2", "Receipts"))
    repo.link(TENANT, "d1", "t1")
    repo.link(TENANT, "d1", "t2")
    repo.link(TENANT, "d2", "t1")
    by_doc = repo.tags_for_documents(TENANT, ["d1", "d2", "d3"])
    assert [t.name for t in by_doc["d1"]] == ["Receipts", "Rome"]  # name-ordered
    assert [t.name for t in by_doc["d2"]] == ["Rome"]
    assert "d3" not in by_doc
