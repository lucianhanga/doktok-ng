import os
from datetime import UTC, datetime

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import EntityRepository
from doktok_contracts.schemas import (
    Document,
    DocumentEntity,
    DocumentStatus,
    EntitySummary,
    EntityType,
    TokenSuggestion,
)
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


class FakeEntityRepository:
    def add_entities(self, entities: list[DocumentEntity]) -> None: ...

    def delete_for_document(self, tenant_id: str, document_id: str) -> None: ...

    def list_for_document(self, tenant_id: str, document_id: str) -> list[DocumentEntity]:
        return []

    def suggest_tokens(
        self,
        tenant_id: str,
        prefix: str,
        *,
        selected: list[str] | None = None,
        limit: int = 10,
    ) -> list[TokenSuggestion]:
        return []

    def documents_for_tokens(
        self,
        tenant_id: str,
        tokens: list[str],
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]:
        return []

    def list_distinct(
        self,
        tenant_id: str,
        *,
        entity_type: EntityType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EntitySummary]:
        items = [
            EntitySummary(
                entity_type=EntityType.EMAIL,
                normalized_value="a@b.com",
                document_count=2,
                occurrences=3,
            ),
            EntitySummary(
                entity_type=EntityType.MONEY,
                normalized_value="$50",
                document_count=1,
                occurrences=1,
            ),
        ]
        if entity_type is not None:
            items = [i for i in items if i.entity_type == entity_type]
        return items

    def documents_for_entity(
        self,
        tenant_id: str,
        entity_type: EntityType,
        normalized_value: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]:
        return [
            Document(
                id="doc1",
                tenant_id=tenant_id,
                sha256="a" * 64,
                original_filename="invoice.txt",
                status=DocumentStatus.ACTIVE,
                created_at=datetime.now(UTC),
            )
        ]


def _client() -> TestClient:
    registry = build_registry()
    registry.register(EntityRepository, FakeEntityRepository())  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tok-a"}


def test_requires_token() -> None:
    assert _client().get("/api/v1/entities").status_code == 401


def test_list_entities_and_filter_by_type() -> None:
    client = _client()
    assert len(client.get("/api/v1/entities", headers=_auth()).json()) == 2
    only_email = client.get("/api/v1/entities?type=EMAIL", headers=_auth()).json()
    assert [e["entity_type"] for e in only_email] == ["EMAIL"]


def test_documents_for_entity() -> None:
    client = _client()
    rows = client.get("/api/v1/entities/documents?type=EMAIL&value=a@b.com", headers=_auth()).json()
    assert [r["original_filename"] for r in rows] == ["invoice.txt"]
