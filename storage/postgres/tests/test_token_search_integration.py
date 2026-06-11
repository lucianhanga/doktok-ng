"""Integration tests for faceted token suggest + AND search (test* tenants only)."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import Document, DocumentEntity, DocumentStatus, EntityType
from doktok_storage_postgres import Database, PostgresDocumentRepository, PostgresEntityRepository

TENANT = "test-a"


def _doc(repo: PostgresDocumentRepository, doc_id: str) -> None:
    repo.add(
        Document(
            id=doc_id,
            tenant_id=TENANT,
            sha256=(doc_id + "a" * 64)[:64],
            original_filename=f"{doc_id}.txt",
            status=DocumentStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
    )


def _tok(eid: str, doc_id: str, value: str) -> DocumentEntity:
    return DocumentEntity(
        id=eid,
        tenant_id=TENANT,
        document_id=doc_id,
        version_id="",
        entity_text=value,
        entity_type=EntityType.CUSTOM_TOKEN,
        normalized_value=value,
        frequency=1,
    )


def _seed(db: Database) -> PostgresEntityRepository:
    docs = PostgresDocumentRepository(db)
    _doc(docs, "d1")
    _doc(docs, "d2")
    repo = PostgresEntityRepository(db)
    repo.add_entities(
        [
            _tok("e1", "d1", "Lucian"),  # mixed case on purpose
            _tok("e2", "d1", "luxury"),
            _tok("e3", "d1", "finance"),
            _tok("e4", "d2", "Lucian"),
            _tok("e5", "d2", "logistics"),
        ]
    )
    return repo


def test_suggest_prefix_is_case_insensitive_and_ranked(db: Database) -> None:
    repo = _seed(db)
    out = repo.suggest_tokens(TENANT, "LU")  # upper-case prefix
    pairs = [(s.value, s.document_count) for s in out]
    assert pairs == [("Lucian", 2), ("luxury", 1)]  # lucian in 2 docs ranks first


def test_suggest_narrows_to_documents_with_selected_tokens(db: Database) -> None:
    repo = _seed(db)
    out = repo.suggest_tokens(TENANT, "lu", selected=["lucian"])
    # Only tokens co-occurring with 'lucian', prefix 'lu', excluding the selected token itself.
    assert [s.value for s in out] == ["luxury"]


def test_documents_for_tokens_is_and_and_case_insensitive(db: Database) -> None:
    repo = _seed(db)
    both = repo.documents_for_tokens(TENANT, ["LUCIAN", "FINANCE"])
    assert {d.id for d in both} == {"d1"}  # only d1 has both
    just_lucian = repo.documents_for_tokens(TENANT, ["lucian"])
    assert {d.id for d in just_lucian} == {"d1", "d2"}
    none = repo.documents_for_tokens(TENANT, [])
    assert none == []
