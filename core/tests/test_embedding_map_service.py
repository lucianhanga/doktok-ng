"""EmbeddingMapService: assemble the colored map + status from in-memory adapters (M7.1)."""

from __future__ import annotations

from doktok_contracts.schemas import DocumentChunk
from doktok_core.categories.inmemory import InMemoryCategoryRepository
from doktok_core.indexing.inmemory import InMemoryChunkRepository
from doktok_core.visualizations.inmemory import (
    InMemoryEmbeddingProjectionRepository,
    InMemoryProjectionRequestRepository,
)
from doktok_core.visualizations.map_service import UNCATEGORIZED, EmbeddingMapService
from doktok_core.visualizations.service import ProjectionService

TENANT = "t1"


class FakeReducer:
    def reduce(self, vectors: list[list[float]], dim: int) -> list[list[float]]:
        return [[float(i)] * dim for i in range(len(vectors))]


def _fixture() -> tuple[
    InMemoryEmbeddingProjectionRepository,
    InMemoryChunkRepository,
    InMemoryCategoryRepository,
    InMemoryProjectionRequestRepository,
]:
    chunks = InMemoryChunkRepository()
    # d0 -> Invoices, d1 -> Invoices, d2 -> no category (Uncategorized).
    chunk_objs = [
        DocumentChunk(
            id=f"c{i}",
            tenant_id=TENANT,
            document_id=f"d{i}",
            version_id="v1",
            text=f"body of doc {i} " * 30,
        )
        for i in range(3)
    ]
    chunks.add_chunks(chunk_objs, [[float(i), 1.0, 0.0] for i in range(3)])

    categories = InMemoryCategoryRepository()
    invoices = categories.create(TENANT, "Invoices", "invoices")
    contracts = categories.create(TENANT, "Contracts", "contracts")
    assert invoices and contracts
    categories.set_document_categories(TENANT, "d0", [invoices.id])
    categories.set_document_categories(TENANT, "d1", [invoices.id, contracts.id])
    # d2 intentionally left uncategorized.

    projections = InMemoryEmbeddingProjectionRepository()
    requests = InMemoryProjectionRequestRepository()
    ProjectionService(chunks, FakeReducer(), projections, algorithm="umap").recompute(TENANT)
    return projections, chunks, categories, requests


def _service(
    projections: InMemoryEmbeddingProjectionRepository,
    chunks: InMemoryChunkRepository,
    categories: InMemoryCategoryRepository,
    requests: InMemoryProjectionRequestRepository,
) -> EmbeddingMapService:
    return EmbeddingMapService(
        projections, chunks, categories, requests, algorithm="umap", snippet_chars=20
    )


def test_map_colors_points_by_primary_category_with_snippets() -> None:
    service = _service(*_fixture())

    result = service.get_map(TENANT, 2)

    assert result.computed is True and result.dim == 2 and len(result.points) == 3
    by_doc = {p.document_id: p for p in result.points}
    assert by_doc["d0"].category == "Invoices"
    assert by_doc["d1"].category == "Invoices"  # primary = higher tenant-wide count (Invoices=2)
    assert by_doc["d2"].category == UNCATEGORIZED
    # Snippets are present and capped.
    assert by_doc["d0"].snippet and len(by_doc["d0"].snippet) <= 21
    # Legend covers exactly the categories present, each with a distinct color.
    legend = {e.category: e.color for e in result.legend}
    assert set(legend) == {"Invoices", UNCATEGORIZED}
    assert legend["Invoices"] != legend[UNCATEGORIZED]
    assert result.meta is not None and result.meta.stale is False


def test_2d_and_3d_share_the_legend_colors() -> None:
    service = _service(*_fixture())

    legend2 = {e.category: e.color for e in service.get_map(TENANT, 2).legend}
    legend3 = {e.category: e.color for e in service.get_map(TENANT, 3).legend}

    assert legend2 == legend3  # server-owned palette: 2D and 3D agree
    assert all(p.z is not None for p in service.get_map(TENANT, 3).points)


def test_status_reports_not_computed_then_fresh() -> None:
    projections, chunks, categories, requests = _fixture()
    service = _service(projections, chunks, categories, requests)

    status = service.get_status(TENANT)
    assert status.recompute_pending is False
    assert {d.dim: d.computed for d in status.dims} == {2: True, 3: True}
    assert all(d.stale is False for d in status.dims)

    # An empty tenant has nothing computed.
    empty = _service(
        InMemoryEmbeddingProjectionRepository(),
        InMemoryChunkRepository(),
        InMemoryCategoryRepository(),
        InMemoryProjectionRequestRepository(),
    )
    empty_map = empty.get_map(TENANT, 2)
    assert empty_map.computed is False and empty_map.points == []


def test_recompute_request_shows_as_pending() -> None:
    projections, chunks, categories, requests = _fixture()
    service = _service(projections, chunks, categories, requests)

    assert service.get_status(TENANT).recompute_pending is False
    service.request_recompute(TENANT)
    assert service.get_status(TENANT).recompute_pending is True
    assert service.get_map(TENANT, 2).recompute_pending is True
