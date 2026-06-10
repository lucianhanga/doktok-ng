"""Integration tests for the Postgres document repository (skipped without a DB)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import psycopg
import pytest
from doktok_contracts.schemas import Document, DocumentStatus
from doktok_storage_postgres import Database, PostgresDocumentRepository, migrate

DSN = os.environ.get("DOKTOK_DATABASE_URL", "postgresql://doktok:doktok@localhost:5432/doktok")


@pytest.fixture
def db() -> Iterator[Database]:
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"postgres not reachable: {exc}")
    database = Database(DSN)
    migrate(database)
    with database.connection() as conn:
        conn.execute("TRUNCATE documents")
    yield database
    database.close()


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
    repo.add(_doc("a-doc", "tenant-a"))
    repo.add(_doc("b-doc", "tenant-b"))

    fetched = repo.get("tenant-a", "a-doc")
    assert fetched is not None
    assert fetched.status is DocumentStatus.ACTIVE
    assert fetched.metadata == {"page_count": 1}

    assert [d.id for d in repo.list_documents("tenant-a")] == ["a-doc"]
    assert repo.get("tenant-a", "b-doc") is None
