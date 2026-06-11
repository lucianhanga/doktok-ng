"""Integration test for failed/duplicate document records (test* tenants only)."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import Document, DocumentStatus
from doktok_storage_postgres import Database, PostgresDocumentRepository

TENANT = "test-a"


def _doc(doc_id: str, status: DocumentStatus, **kw: object) -> Document:
    return Document(
        id=doc_id,
        tenant_id=TENANT,
        sha256="a" * 64,
        original_filename=f"{doc_id}.pdf",
        status=status,
        created_at=datetime.now(UTC),
        **kw,  # type: ignore[arg-type]
    )


def test_duplicate_document_persists_link_to_original(db: Database) -> None:
    repo = PostgresDocumentRepository(db)
    repo.add(_doc("orig", DocumentStatus.ACTIVE))
    repo.add(_doc("dup", DocumentStatus.DUPLICATE, duplicate_of="orig"))

    dup = repo.get(TENANT, "dup")
    assert dup is not None
    assert dup.status is DocumentStatus.DUPLICATE
    assert dup.duplicate_of == "orig"

    original = repo.get(TENANT, "orig")
    assert original is not None and original.duplicate_of is None
