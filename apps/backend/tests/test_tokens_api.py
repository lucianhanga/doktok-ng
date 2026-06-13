import os
from datetime import UTC, datetime

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import EntityRepository
from doktok_contracts.schemas import Document, DocumentStatus, TokenSuggestion
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
    def __init__(self) -> None:
        self.suggest_args: tuple[str, str, list[str]] | None = None
        self.search_tokens: list[str] | None = None

    def add_entities(self, entities: list) -> None: ...  # type: ignore[type-arg]
    def delete_for_document(self, tenant_id: str, document_id: str) -> None: ...
    def delete_for_document_types(self, tenant_id, document_id, entity_types) -> None: ...  # type: ignore[no-untyped-def]
    def list_for_document(self, tenant_id: str, document_id: str) -> list:  # type: ignore[type-arg]
        return []

    def list_distinct(self, tenant_id, *, entity_type=None, limit=100, offset=0):  # type: ignore[no-untyped-def]
        return []

    def documents_for_entity(self, tenant_id, entity_type, normalized_value, *, limit=50, offset=0):  # type: ignore[no-untyped-def]
        return []

    def suggest_tokens(
        self,
        tenant_id: str,
        prefix: str,
        *,
        selected: list[str] | None = None,
        limit: int = 10,
    ) -> list[TokenSuggestion]:
        self.suggest_args = (tenant_id, prefix, selected or [])
        return [TokenSuggestion(value="lucian", document_count=2)]

    def documents_for_tokens(
        self, tenant_id: str, tokens: list[str], *, limit: int = 50, offset: int = 0
    ) -> list[Document]:
        self.search_tokens = tokens
        return [
            Document(
                id="d1",
                tenant_id=tenant_id,
                sha256="a" * 64,
                original_filename="match.txt",
                status=DocumentStatus.ACTIVE,
                created_at=datetime.now(UTC),
            )
        ]


def _client(repo: FakeEntityRepository) -> TestClient:
    registry = build_registry()
    registry.register(EntityRepository, repo)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tok-a"}


def test_suggest_requires_token() -> None:
    assert (
        _client(FakeEntityRepository()).get("/api/v1/tokens/suggest?prefix=lu").status_code == 401
    )


def test_suggest_passes_prefix_and_selected() -> None:
    repo = FakeEntityRepository()
    body = (
        _client(repo)
        .get("/api/v1/tokens/suggest?prefix=lu&token=finance&token=lucian", headers=_auth())
        .json()
    )
    assert body[0]["value"] == "lucian"
    assert repo.suggest_args == ("tenant-a", "lu", ["finance", "lucian"])


def test_search_returns_matching_documents() -> None:
    repo = FakeEntityRepository()
    body = (
        _client(repo)
        .get("/api/v1/tokens/search?token=lucian&token=finance", headers=_auth())
        .json()
    )
    assert [d["original_filename"] for d in body] == ["match.txt"]
    assert repo.search_tokens == ["lucian", "finance"]


def test_search_with_no_tokens_is_empty() -> None:
    body = _client(FakeEntityRepository()).get("/api/v1/tokens/search", headers=_auth()).json()
    assert body == []
