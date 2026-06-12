"""ProjectionService + ProjectionRunner with in-memory adapters (ADR-0016, M7.1)."""

from __future__ import annotations

from doktok_contracts.schemas import DocumentChunk
from doktok_core.indexing.inmemory import InMemoryChunkRepository
from doktok_core.visualizations.inmemory import (
    InMemoryEmbeddingProjectionRepository,
    InMemoryProjectionRequestRepository,
)
from doktok_core.visualizations.service import ProjectionRunner, ProjectionService

TENANT = "t1"


class FakeReducer:
    """Deterministic stand-in: maps each vector to ``[i, i, ...]`` so coords are predictable."""

    def reduce(self, vectors: list[list[float]], dim: int) -> list[list[float]]:
        return [[float(i)] * dim for i in range(len(vectors))]


def _chunk_repo(tenant: str, n: int) -> InMemoryChunkRepository:
    repo = InMemoryChunkRepository()
    chunks = [
        DocumentChunk(
            id=f"{tenant}-c{i:03d}",
            tenant_id=tenant,
            document_id=f"d{i % 2}",
            version_id="v1",
            text=f"chunk {i}",
        )
        for i in range(n)
    ]
    repo.add_chunks(chunks, [[float(i), 1.0, 0.0] for i in range(n)])
    return repo


def _service(
    chunks: InMemoryChunkRepository,
    projections: InMemoryEmbeddingProjectionRepository,
    **kw: int,
) -> ProjectionService:
    return ProjectionService(chunks, FakeReducer(), projections, algorithm="umap", **kw)


def test_recompute_writes_2d_and_3d_projections() -> None:
    projections = InMemoryEmbeddingProjectionRepository()
    service = _service(_chunk_repo(TENANT, 5), projections)

    n = service.recompute(TENANT)

    assert n == 5
    p2 = projections.get(TENANT, 2)
    p3 = projections.get(TENANT, 3)
    assert p2 is not None and p2.n_points == 5 and all(pt.z is None for pt in p2.points)
    assert p3 is not None and p3.n_points == 5 and all(pt.z is not None for pt in p3.points)
    # Points keep their chunk/document identity and are ordered by chunk id.
    assert p2.points[0].chunk_id == f"{TENANT}-c000"
    assert {pt.document_id for pt in p2.points} == {"d0", "d1"}


def test_recompute_truncates_past_the_cap_and_flags_it() -> None:
    projections = InMemoryEmbeddingProjectionRepository()
    service = _service(_chunk_repo(TENANT, 10), projections, max_points=4)

    service.recompute(TENANT)

    p2 = projections.get(TENANT, 2)
    assert p2 is not None and p2.n_points == 4 and p2.truncated is True


def test_is_stale_tracks_the_fingerprint() -> None:
    chunks = _chunk_repo(TENANT, 3)
    projections = InMemoryEmbeddingProjectionRepository()
    service = _service(chunks, projections)

    assert service.is_stale(TENANT, 2) is True  # nothing cached yet
    service.recompute(TENANT)
    assert service.is_stale(TENANT, 2) is False  # fresh
    chunks.add_chunks(
        [
            DocumentChunk(
                id=f"{TENANT}-c999", tenant_id=TENANT, document_id="d9", version_id="v1", text="x"
            )
        ],
        [[9.0, 1.0, 0.0]],
    )
    assert service.is_stale(TENANT, 2) is True  # inputs changed -> stale until recompute


def test_runner_drains_the_queue() -> None:
    requests = InMemoryProjectionRequestRepository()
    projections = InMemoryEmbeddingProjectionRepository()
    runner = ProjectionRunner(requests, _service(_chunk_repo(TENANT, 3), projections))
    requests.request(TENANT)
    requests.request(TENANT)  # idempotent: still one pending

    assert runner.run_pending() == 1
    assert projections.get(TENANT, 2) is not None
    assert requests.has_pending(TENANT) is False
    assert runner.run_pending() == 0  # queue drained
