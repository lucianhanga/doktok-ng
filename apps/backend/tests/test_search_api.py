import os

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import Retriever
from doktok_contracts.schemas import SearchHit
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


class FakeRetriever:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits
        self.calls: list[tuple[str, str, int]] = []

    def search(self, tenant_id, query, limit=10, *, filters=None):  # type: ignore[no-untyped-def]
        self.calls.append((tenant_id, query, limit))
        return self._hits


def _client(retriever: FakeRetriever) -> TestClient:
    registry = build_registry()
    registry.register(Retriever, retriever)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _hit() -> SearchHit:
    return SearchHit(
        document_id="doc1",
        chunk_id="c1",
        original_filename="report.pdf",
        title="report",
        page_start=2,
        page_end=2,
        snippet="quarterly revenue grew",
        score=0.5,
    )


def test_requires_token() -> None:
    assert _client(FakeRetriever([])).get("/api/v1/search?q=hi").status_code == 401


def test_query_is_required() -> None:
    client = _client(FakeRetriever([]))
    assert (
        client.get("/api/v1/search", headers={"Authorization": "Bearer tok-a"}).status_code == 422
    )


def test_search_returns_hits_for_caller_tenant() -> None:
    retriever = FakeRetriever([_hit()])
    client = _client(retriever)
    response = client.get("/api/v1/search?q=revenue", headers={"Authorization": "Bearer tok-a"})
    assert response.status_code == 200
    assert response.json()[0]["snippet"] == "quarterly revenue grew"
    assert retriever.calls == [("tenant-a", "revenue", 10)]


def test_search_query_is_length_bounded() -> None:
    # F-39 (#651): an absurd-length q costs DB CPU per request - 422 before any scan.
    client = _client(FakeRetriever([]))
    resp = client.get(f"/api/v1/search?q={'x' * 501}", headers={"Authorization": "Bearer tok-a"})
    assert resp.status_code == 422
    ok = client.get(f"/api/v1/search?q={'x' * 500}", headers={"Authorization": "Bearer tok-a"})
    assert ok.status_code == 200
