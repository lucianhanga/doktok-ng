"""Integration tests for the Postgres document repository.

Uses only ``test*`` tenants; cleanup is scoped in conftest. Skipped without a database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from doktok_contracts.errors import DuplicateActiveDocumentError
from doktok_contracts.schemas import (
    Document,
    DocumentChunk,
    DocumentEntity,
    DocumentSort,
    DocumentStatus,
    EntityType,
    SortDir,
)
from doktok_storage_postgres import (
    Database,
    PostgresChunkRepository,
    PostgresDocumentRepository,
    PostgresEntityRepository,
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


def test_find_active_by_sha_and_duplicate_translation(db: Database) -> None:
    repo = PostgresDocumentRepository(db)
    sha = "deadbeef" * 8
    original = Document(
        id="dedup-1",
        tenant_id=TEST_TENANT_PAGE,
        sha256=sha,
        original_filename="a.pdf",
        status=DocumentStatus.ACTIVE,
        created_at=datetime.now(UTC),
    )
    repo.add(original)
    assert repo.find_active_by_sha256(TEST_TENANT_PAGE, sha) == "dedup-1"
    assert repo.find_active_by_sha256(TEST_TENANT_PAGE, "f" * 64) is None

    # A second ACTIVE doc with the same content is a domain duplicate, not a raw DB error.
    clash = original.model_copy(update={"id": "dedup-2", "original_filename": "b.pdf"})
    with pytest.raises(DuplicateActiveDocumentError):
        repo.add(clash)


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


def test_unidentifiable_round_trips_and_filters(db: Database) -> None:
    repo = PostgresDocumentRepository(db)
    flagged = _doc("u-doc", TEST_TENANT_A)
    flagged.unidentifiable = True
    fine = _doc("a-doc", TEST_TENANT_A)
    fine.unidentifiable = False
    unassessed = _doc("n-doc", TEST_TENANT_A)  # unidentifiable stays NULL
    for d in (flagged, fine, unassessed):
        d.sha256 = (d.id + "z" * 64)[:64]  # distinct content hash (active-sha uniqueness)
        repo.add(d)

    # Round-trips through the explicit column list.
    assert repo.get(TEST_TENANT_A, "u-doc").unidentifiable is True  # type: ignore[union-attr]
    assert repo.get(TEST_TENANT_A, "n-doc").unidentifiable is None  # type: ignore[union-attr]

    only, total, _ = repo.list_documents(TEST_TENANT_A, unidentifiable=True)
    assert {d.id for d in only} == {"u-doc"} and total == 1
    excl, _, _ = repo.list_documents(TEST_TENANT_A, unidentifiable=False)
    assert {d.id for d in excl} == {"a-doc", "n-doc"}  # NULL 'unassessed' stays shown
    ids, _, _ = repo.list_document_ids(TEST_TENANT_A, unidentifiable=True)
    assert ids == ["u-doc"]


def test_needs_attention_filter_flags_failed_not_in_progress(db: Database) -> None:
    repo = PostgresDocumentRepository(db)
    feats = PostgresFeatureRepository(db)
    base = datetime(2024, 2, 1, tzinfo=UTC)
    for i in range(3):
        repo.add(_page_doc(f"a{i}", when=base + timedelta(minutes=i)))
    feats.ensure_for_active(TEST_TENANT_PAGE, [("doc_metadata", 1)])  # pending for a0, a1, a2
    feats.record_done(TEST_TENANT_PAGE, "a0", "doc_metadata", 1)  # a0 done
    # a1 -> FAILED (a real problem); a2 stays pending (still processing).
    now = datetime.now(UTC)
    claimed = feats.claim_next(TEST_TENANT_PAGE, now=now, reclaim_before=now - timedelta(hours=1))
    assert claimed is not None and claimed.document_id == "a1"  # oldest pending
    feats.mark_failed(claimed.id, error="boom", next_attempt_at=now + timedelta(hours=1))

    items, total, _ = repo.list_documents(TEST_TENANT_PAGE, needs_attention=True)
    ids = {d.id for d in items}
    # Only the FAILED document; the done one and the still-pending (in-processing) one are excluded.
    assert ids == {"a1"} and total == 1


def test_sort_by_status(db: Database) -> None:
    repo = PostgresDocumentRepository(db)
    base = datetime(2024, 3, 1, tzinfo=UTC)
    active = _page_doc("s-active", when=base)
    failed = Document(
        id="s-failed",
        tenant_id=TEST_TENANT_PAGE,
        sha256=("s-failed" + "a" * 64)[:64],
        original_filename="s-failed.txt",
        status=DocumentStatus.FAILED,
        created_at=base + timedelta(minutes=1),
    )
    repo.add(active)
    repo.add(failed)

    asc_items, _, _ = repo.list_documents(
        TEST_TENANT_PAGE, sort=DocumentSort.STATUS, direction=SortDir.ASC
    )
    statuses = [d.status.value for d in asc_items]
    assert statuses == sorted(statuses)


def test_sort_by_entities_and_cursor_round_trip(db: Database) -> None:
    repo = PostgresDocumentRepository(db)
    entity_repo = PostgresEntityRepository(db)
    base = datetime(2024, 4, 1, tzinfo=UTC)

    for i in range(4):
        doc = _page_doc(f"ent{i}", when=base + timedelta(minutes=i))
        repo.add(doc)
        for j in range(i):
            entity_repo.add_entities(
                [
                    DocumentEntity(
                        id=f"ent{i}-e{j}",
                        tenant_id=TEST_TENANT_PAGE,
                        document_id=f"ent{i}",
                        version_id="v1",
                        entity_text="foo",
                        entity_type=EntityType.CUSTOM_TOKEN,
                        normalized_value=f"foo{j}",
                    )
                ]
            )

    # ent3 has 3 entities, ent2 has 2, ent1 has 1, ent0 has 0 — DESC order expected
    page1, total, anchor = repo.list_documents(
        TEST_TENANT_PAGE, sort=DocumentSort.ENTITIES, direction=SortDir.DESC, limit=2
    )
    assert total == 4
    assert [d.id for d in page1] == ["ent3", "ent2"]
    assert anchor is not None

    page2, _, anchor2 = repo.list_documents(
        TEST_TENANT_PAGE, sort=DocumentSort.ENTITIES, direction=SortDir.DESC, limit=2, cursor=anchor
    )
    assert [d.id for d in page2] == ["ent1", "ent0"]
    assert anchor2 is None


def test_sort_by_chunks_and_cursor_round_trip(db: Database) -> None:
    repo = PostgresDocumentRepository(db)
    chunk_repo = PostgresChunkRepository(db)
    base = datetime(2024, 5, 1, tzinfo=UTC)

    for i in range(3):
        doc = _page_doc(f"chk{i}", when=base + timedelta(minutes=i))
        repo.add(doc)
        for j in range(i):
            chunk_repo.add_chunks(
                [
                    DocumentChunk(
                        id=f"chk{i}-c{j}",
                        tenant_id=TEST_TENANT_PAGE,
                        document_id=f"chk{i}",
                        version_id="v1",
                        text=f"text {j}",
                    )
                ],
                [[0.1] * 1024],  # document_chunks.embedding is vector(1024)
            )

    # chk2 has 2 chunks, chk1 has 1, chk0 has 0 — DESC order
    page1, total, anchor = repo.list_documents(
        TEST_TENANT_PAGE, sort=DocumentSort.CHUNKS, direction=SortDir.DESC, limit=2
    )
    assert total == 3
    assert [d.id for d in page1] == ["chk2", "chk1"]
    assert anchor is not None

    page2, _, anchor2 = repo.list_documents(
        TEST_TENANT_PAGE, sort=DocumentSort.CHUNKS, direction=SortDir.DESC, limit=2, cursor=anchor
    )
    assert [d.id for d in page2] == ["chk0"]
    assert anchor2 is None


def test_title_source_guards_metadata_overwrite(db: Database) -> None:
    """#537: set_metadata (the auto/LLM path) never overwrites a manual title; set_title marks a
    rename; clear_manual_title hands the title back to the auto path."""
    repo = PostgresDocumentRepository(db)
    repo.add(_doc("t537-doc", TEST_TENANT_A))

    # Default: the auto path owns the title.
    fetched = repo.get(TEST_TENANT_A, "t537-doc")
    assert fetched is not None and fetched.title_source == "auto"

    # A manual rename marks title_source and wins over the next metadata run.
    repo.set_title(TEST_TENANT_A, "t537-doc", "My own name")
    repo.set_metadata(
        TEST_TENANT_A,
        "t537-doc",
        title="LLM title",
        document_date=None,
        location=None,
        summary="new summary",
    )
    fetched = repo.get(TEST_TENANT_A, "t537-doc")
    assert fetched is not None
    assert fetched.title == "My own name"
    assert fetched.title_source == "manual"
    assert fetched.summary == "new summary"  # other enrichment fields still update

    # Clearing hands it back: the next metadata run re-derives the title.
    repo.clear_manual_title(TEST_TENANT_A, "t537-doc")
    repo.set_metadata(
        TEST_TENANT_A,
        "t537-doc",
        title="LLM title",
        document_date=None,
        location=None,
        summary="s2",
    )
    fetched = repo.get(TEST_TENANT_A, "t537-doc")
    assert fetched is not None
    assert fetched.title_source == "auto"
    assert fetched.title == "LLM title"
