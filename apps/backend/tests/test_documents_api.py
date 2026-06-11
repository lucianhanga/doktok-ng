import os
from datetime import UTC, datetime

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import CategoryRepository, DocumentRepository
from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.categories import InMemoryCategoryRepository
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a", "tok-b": "tenant-b"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


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
    )


def _client(*docs: Document) -> TestClient:
    repo = InMemoryDocumentRepository()
    for doc in docs:
        repo.add(doc)
    registry = build_registry()
    registry.register(DocumentRepository, repo)  # type: ignore[type-abstract]
    registry.register(CategoryRepository, InMemoryCategoryRepository())  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_requires_token() -> None:
    client = _client(_doc("d1", "tenant-a"))
    assert client.get("/api/v1/documents").status_code == 401


def test_status_filter() -> None:
    active = _doc("a1", "tenant-a")
    failed = _doc("f1", "tenant-a")
    failed.status = DocumentStatus.FAILED
    client = _client(active, failed)
    all_ids = {d["id"] for d in client.get("/api/v1/documents", headers=_auth("tok-a")).json()}
    assert all_ids == {"a1", "f1"}
    failed_only = client.get("/api/v1/documents?status=failed", headers=_auth("tok-a")).json()
    assert [d["id"] for d in failed_only] == ["f1"]


def test_lists_only_callers_tenant() -> None:
    client = _client(_doc("a-doc", "tenant-a"), _doc("b-doc", "tenant-b"))
    response = client.get("/api/v1/documents", headers=_auth("tok-a"))
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == ["a-doc"]


def test_cannot_read_another_tenants_document() -> None:
    client = _client(_doc("a-doc", "tenant-a"), _doc("b-doc", "tenant-b"))
    assert client.get("/api/v1/documents/b-doc", headers=_auth("tok-a")).status_code == 404
    assert client.get("/api/v1/documents/a-doc", headers=_auth("tok-a")).status_code == 200
