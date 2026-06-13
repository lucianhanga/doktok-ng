"""Embedding-map API (M7.1): tenant-scoped read / status / recompute over in-memory repos."""

import os

import pytest
from doktok_api.main import create_app
from doktok_contracts.media import ProjectionResult
from doktok_contracts.ports import (
    CategoryRepository,
    ChunkRepository,
    EmbeddingProjectionRepository,
    ProjectionRequestRepository,
)
from doktok_contracts.schemas import DocumentChunk
from doktok_core.categories.inmemory import InMemoryCategoryRepository
from doktok_core.config import Settings
from doktok_core.indexing.inmemory import InMemoryChunkRepository
from doktok_core.registry import build_registry
from doktok_core.visualizations.inmemory import (
    InMemoryEmbeddingProjectionRepository,
    InMemoryProjectionRequestRepository,
)
from doktok_core.visualizations.service import ProjectionService
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}
TENANT = "tenant-a"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


class FakeProjector:
    def project(self, vectors, dims):  # type: ignore[no-untyped-def]
        coords = {int(d): [[float(i)] * int(d) for i in range(len(vectors))] for d in dims}
        return ProjectionResult(coords=coords, clusters=[i % 2 for i in range(len(vectors))])

    def prewarm(self) -> None:
        pass


def _client(*, computed: bool = True) -> tuple[TestClient, InMemoryProjectionRequestRepository]:
    chunks = InMemoryChunkRepository()
    chunks.add_chunks(
        [
            DocumentChunk(
                id="c0", tenant_id=TENANT, document_id="d0", version_id="v1", text="alpha"
            ),
            DocumentChunk(
                id="c1", tenant_id=TENANT, document_id="d1", version_id="v1", text="beta"
            ),
        ],
        [[0.0, 1.0, 0.0], [1.0, 0.0, 1.0]],
    )
    categories = InMemoryCategoryRepository()
    cat = categories.create(TENANT, "Invoices", "invoices")
    assert cat
    categories.set_document_categories(TENANT, "d0", [cat.id])

    projections = InMemoryEmbeddingProjectionRepository()
    requests = InMemoryProjectionRequestRepository()
    if computed:
        # Match the API's projection_version (Settings default) so the cached map reads as fresh.
        version = Settings(_env_file=None).projection_version  # type: ignore[call-arg]
        ProjectionService(
            chunks, FakeProjector(), projections, algorithm="umap", version=version
        ).recompute(TENANT)

    registry = build_registry()
    registry.register(EmbeddingProjectionRepository, projections)  # type: ignore[type-abstract]
    registry.register(ChunkRepository, chunks)  # type: ignore[type-abstract]
    registry.register(CategoryRepository, categories)  # type: ignore[type-abstract]
    registry.register(ProjectionRequestRepository, requests)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry)), requests


def test_requires_token() -> None:
    client, _ = _client()
    assert client.get("/api/v1/visualizations/embeddings").status_code == 401
    assert client.get("/api/v1/visualizations/embeddings/status").status_code == 401
    assert client.post("/api/v1/visualizations/embeddings/recompute").status_code == 401


def test_returns_colored_map() -> None:
    client, _ = _client()
    body = client.get(
        "/api/v1/visualizations/embeddings?dim=2", headers={"Authorization": "Bearer tok-a"}
    ).json()
    assert body["computed"] is True and body["dim"] == 2 and len(body["points"]) == 2
    cats = {p["document_id"]: p["category"] for p in body["points"]}
    assert cats == {"d0": "Invoices", "d1": "Uncategorized"}
    assert body["meta"]["stale"] is False
    assert {e["category"] for e in body["legend"]} == {"Invoices", "Uncategorized"}


def test_dim_is_validated() -> None:
    client, _ = _client()
    r = client.get(
        "/api/v1/visualizations/embeddings?dim=4", headers={"Authorization": "Bearer tok-a"}
    )
    assert r.status_code == 422


def test_status_and_recompute() -> None:
    client, requests = _client(computed=False)
    status = client.get(
        "/api/v1/visualizations/embeddings/status", headers={"Authorization": "Bearer tok-a"}
    ).json()
    assert status["recompute_pending"] is False
    assert {d["dim"]: d["computed"] for d in status["dims"]} == {2: False, 3: False}

    r = client.post(
        "/api/v1/visualizations/embeddings/recompute", headers={"Authorization": "Bearer tok-a"}
    )
    assert r.status_code == 202
    assert requests.has_pending(TENANT) is True
