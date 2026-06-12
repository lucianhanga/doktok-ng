"""Embedding-space visualization for the Insights tab (M7.1, ADR-0016). Tenant-scoped.

Serves the cached 2D/3D projection as a colored point cloud, reports cache/staleness status, and
enqueues a recompute. The projection itself is fitted by the worker; these endpoints only read the
cache and the recompute queue.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from doktok_contracts.schemas import EmbeddingMap, ProjectionStatus
from fastapi import APIRouter, Depends, Query, Response, status

from doktok_api.dependencies import Tenant, get_embedding_map_service

if TYPE_CHECKING:
    from doktok_core.visualizations.map_service import EmbeddingMapService

router = APIRouter(prefix="/api/v1/visualizations/embeddings", tags=["visualizations"])

Service = Annotated["EmbeddingMapService", Depends(get_embedding_map_service)]


@router.get("", response_model=EmbeddingMap)
def get_embedding_map(
    tenant: Tenant,
    service: Service,
    dim: Annotated[int, Query(ge=2, le=3)] = 2,
) -> EmbeddingMap:
    """The embedding map for ``dim`` (2 or 3): points colored by primary category + a legend.

    ``computed`` is False until the worker has fitted a projection; ``meta.stale`` flags a cache
    no longer matching the current embeddings; ``recompute_pending`` is True while one is in flight.
    """
    return service.get_map(tenant.tenant_id, dim)


@router.get("/status", response_model=ProjectionStatus)
def get_projection_status(tenant: Tenant, service: Service) -> ProjectionStatus:
    """Per-dimension cache state (computed/stale/point count) and whether a recompute is queued."""
    return service.get_status(tenant.tenant_id)


@router.post("/recompute", status_code=status.HTTP_202_ACCEPTED)
def request_recompute(tenant: Tenant, service: Service) -> Response:
    """Enqueue a recompute of the tenant's 2D + 3D projections (idempotent while one is pending)."""
    service.request_recompute(tenant.tenant_id)
    return Response(status_code=status.HTTP_202_ACCEPTED)
