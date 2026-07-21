"""Integration tests for the Postgres document_notes store (#736; test* tenants)."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import Document, DocumentNote, DocumentStatus
from doktok_storage_postgres import (
    Database,
    PostgresDocumentNoteRepository,
    PostgresDocumentRepository,
)

TENANT = "test-notes"


def _doc(doc_id: str) -> Document:
    return Document(
        id=doc_id,
        tenant_id=TENANT,
        sha256=(doc_id + "a" * 64)[:64],
        original_filename=f"{doc_id}.pdf",
        status=DocumentStatus.ACTIVE,
        created_at=datetime.now(UTC),
    )


def _note(note_id: str, doc_id: str, body: str, author: str = "u1") -> DocumentNote:
    return DocumentNote(
        id=note_id,
        tenant_id=TENANT,
        document_id=doc_id,
        author_id=author,
        author_email=f"{author}@x.com",
        body=body,
        created_at=datetime.now(UTC),
    )


def test_notes_round_trip_newest_first_and_tenant_isolation(db: Database) -> None:
    PostgresDocumentRepository(db).add(_doc("n-doc"))
    repo = PostgresDocumentNoteRepository(db)
    repo.add_note(_note("n1", "n-doc", "first"))
    repo.add_note(_note("n2", "n-doc", "second"))

    notes = repo.list_for_document(TENANT, "n-doc")
    assert [n.body for n in notes] == ["second", "first"]  # newest first
    fetched = repo.get_note(TENANT, "n1")
    assert fetched is not None and fetched.author_email == "u1@x.com"
    assert repo.get_note("test-other", "n1") is None  # tenant isolation
    assert repo.list_for_document(TENANT, "n-other") == []

    repo.delete_note(TENANT, "n1")
    assert repo.get_note(TENANT, "n1") is None
    assert [n.body for n in repo.list_for_document(TENANT, "n-doc")] == ["second"]


def test_notes_cascade_with_the_document(db: Database) -> None:
    PostgresDocumentRepository(db).add(_doc("n-casc"))
    repo = PostgresDocumentNoteRepository(db)
    repo.add_note(_note("n9", "n-casc", "gone with the doc"))
    PostgresDocumentRepository(db).delete(TENANT, "n-casc")
    assert repo.get_note(TENANT, "n9") is None
