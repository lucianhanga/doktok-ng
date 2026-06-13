"""Embedding-projection service: fit + cache a tenant's 2D/3D embedding map (ADR-0016, M7.1).

A projection is a tenant-level aggregate (the reducer fits all of a tenant's chunk embeddings
jointly), so this is a dedicated job - NOT a per-document FeatureProcessor. The worker drains the
recompute queue and calls :meth:`recompute`; the API enqueues requests and reads the cache.
"""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.ports import (
    ChunkRepository,
    EmbeddingProjectionRepository,
    EmbeddingProjector,
    ProjectionRequestRepository,
)
from doktok_contracts.schemas import EmbeddingProjection, ProjectionPoint

_DIMS = (2, 3)


def projection_fingerprint(embedding_fingerprint: str, algorithm: str, version: int) -> str:
    """Fingerprint identifying the inputs of a projection, used to detect staleness."""
    return f"{embedding_fingerprint}|algo={algorithm}|v={version}"


class ProjectionService:
    """Fits and caches the 2D/3D embedding projections for a tenant."""

    def __init__(
        self,
        chunk_repo: ChunkRepository,
        projector: EmbeddingProjector,
        projection_repo: EmbeddingProjectionRepository,
        *,
        algorithm: str,
        version: int = 1,
        max_points: int = 20000,
    ) -> None:
        self._chunks = chunk_repo
        self._projector = projector
        self._projections = projection_repo
        self._algorithm = algorithm
        self._version = version
        self._max_points = max(1, max_points)

    def prewarm(self) -> None:
        self._projector.prewarm()

    def is_stale(self, tenant_id: str, dim: int) -> bool:
        """Whether the cached projection for (tenant, dim) is missing or out of date."""
        cached = self._projections.get_header(tenant_id, dim)
        if cached is None:
            return True
        return cached.input_fingerprint != self._current_fingerprint(tenant_id)

    def recompute(self, tenant_id: str) -> int:
        """Fit and cache every dimension for one tenant. Returns the number of points projected.

        Reads the tenant's chunk embeddings once (capped at ``max_points``), then fits all target
        dimensions in one pass: PCA pre-reduce, cluster once (shared across dims), UMAP per dim.
        """
        rows = self._chunks.read_embeddings(tenant_id, self._max_points + 1)
        truncated = len(rows) > self._max_points
        rows = rows[: self._max_points]
        fingerprint = self._current_fingerprint(tenant_id)
        computed_at = datetime.now(UTC)
        vectors = [embedding for _, _, embedding in rows]
        result = self._projector.project(vectors, _DIMS)

        for dim in _DIMS:
            coords = result.coords.get(dim, [])
            points = [
                ProjectionPoint(
                    chunk_id=rows[i][0],
                    document_id=rows[i][1],
                    x=float(coord[0]),
                    y=float(coord[1]),
                    z=float(coord[2]) if dim == 3 else None,
                    cluster=result.clusters[i] if i < len(result.clusters) else None,
                )
                for i, coord in enumerate(coords)
            ]
            self._projections.upsert(
                EmbeddingProjection(
                    tenant_id=tenant_id,
                    dim=dim,
                    algorithm=self._algorithm,
                    version=self._version,
                    input_fingerprint=fingerprint,
                    n_points=len(points),
                    truncated=truncated,
                    computed_at=computed_at,
                    points=points,
                )
            )
        return len(rows)

    def _current_fingerprint(self, tenant_id: str) -> str:
        return projection_fingerprint(
            self._chunks.embedding_fingerprint(tenant_id), self._algorithm, self._version
        )


class ProjectionRunner:
    """Drains the recompute queue: claim a request, fit the tenant's projections, clear it."""

    def __init__(self, requests: ProjectionRequestRepository, service: ProjectionService) -> None:
        self._requests = requests
        self._service = service

    def prewarm(self) -> None:
        self._service.prewarm()

    def run_pending(self) -> int:
        """Process every queued recompute request. Returns the number of tenants recomputed."""
        processed = 0
        while True:
            request = self._requests.claim_next()
            if request is None:
                return processed
            try:
                self._service.recompute(request.tenant_id)
                processed += 1
            finally:
                # Always clear the claim so one bad tenant cannot wedge the queue.
                self._requests.complete(request.id)
