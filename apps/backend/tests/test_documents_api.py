import os
from datetime import UTC, datetime, timedelta

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import CategoryRepository, DocumentRepository, FeatureRepository
from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.categories import InMemoryCategoryRepository
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.features.inmemory import InMemoryFeatureRepository
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
        sha256=(doc_id + "a" * 64)[:64],  # distinct per doc (active-sha dedup invariant)
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
    registry.register(FeatureRepository, InMemoryFeatureRepository())  # type: ignore[type-abstract]
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
    body = client.get("/api/v1/documents", headers=_auth("tok-a")).json()
    assert {d["id"] for d in body["items"]} == {"a1", "f1"}
    assert body["total"] == 2
    failed_only = client.get("/api/v1/documents?status=failed", headers=_auth("tok-a")).json()
    assert [d["id"] for d in failed_only["items"]] == ["f1"]
    assert failed_only["total"] == 1


def test_lists_only_callers_tenant() -> None:
    client = _client(_doc("a-doc", "tenant-a"), _doc("b-doc", "tenant-b"))
    response = client.get("/api/v1/documents", headers=_auth("tok-a"))
    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body["items"]] == ["a-doc"]
    assert body["total"] == 1 and body["next_cursor"] is None


def test_keyset_pagination_pages_without_overlap() -> None:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    docs = []
    for i in range(5):
        d = _doc(f"d{i}", "tenant-a")
        d.created_at = base + timedelta(minutes=i)  # distinct, ascending
        docs.append(d)
    client = _client(*docs)

    p1 = client.get("/api/v1/documents?limit=2", headers=_auth("tok-a")).json()
    assert [d["id"] for d in p1["items"]] == ["d4", "d3"]  # newest first
    assert p1["total"] == 5 and p1["next_cursor"]

    p2 = client.get(
        f"/api/v1/documents?limit=2&cursor={p1['next_cursor']}", headers=_auth("tok-a")
    ).json()
    assert [d["id"] for d in p2["items"]] == ["d2", "d1"]  # no overlap with page 1

    p3 = client.get(
        f"/api/v1/documents?limit=2&cursor={p2['next_cursor']}", headers=_auth("tok-a")
    ).json()
    assert [d["id"] for d in p3["items"]] == ["d0"]
    assert p3["next_cursor"] is None  # last page


def test_needs_attention_filter() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(_doc("ok", "tenant-a"))
    repo.add(_doc("stuck", "tenant-a"))
    repo.attention_ids = {"stuck"}  # only this doc has a non-done feature
    registry = build_registry()
    registry.register(DocumentRepository, repo)  # type: ignore[type-abstract]
    registry.register(CategoryRepository, InMemoryCategoryRepository())  # type: ignore[type-abstract]
    registry.register(FeatureRepository, InMemoryFeatureRepository())  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    client = TestClient(create_app(settings=settings, registry=registry))

    body = client.get("/api/v1/documents?needs_attention=true", headers=_auth("tok-a")).json()
    assert [d["id"] for d in body["items"]] == ["stuck"]
    assert body["total"] == 1


def test_invalid_cursor_is_400() -> None:
    client = _client(_doc("d1", "tenant-a"))
    resp = client.get("/api/v1/documents?cursor=not-a-cursor", headers=_auth("tok-a"))
    assert resp.status_code == 400


def test_cannot_read_another_tenants_document() -> None:
    client = _client(_doc("a-doc", "tenant-a"), _doc("b-doc", "tenant-b"))
    assert client.get("/api/v1/documents/b-doc", headers=_auth("tok-a")).status_code == 404
    assert client.get("/api/v1/documents/a-doc", headers=_auth("tok-a")).status_code == 200
