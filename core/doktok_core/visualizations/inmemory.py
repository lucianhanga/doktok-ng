"""In-memory embedding-projection cache for tests and local/dev runs (ADR-0016, M7.1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from doktok_contracts.schemas import EmbeddingProjection, ProjectionRequest


class InMemoryEmbeddingProjectionRepository:
    """One cached projection per (tenant_id, dim); upsert replaces, like the Postgres adapter."""

    def __init__(self) -> None:
        self.projections: dict[tuple[str, int], EmbeddingProjection] = {}

    def upsert(self, projection: EmbeddingProjection) -> None:
        self.projections[(projection.tenant_id, projection.dim)] = projection

    def get(self, tenant_id: str, dim: int) -> EmbeddingProjection | None:
        return self.projections.get((tenant_id, dim))

    def get_header(self, tenant_id: str, dim: int) -> EmbeddingProjection | None:
        projection = self.projections.get((tenant_id, dim))
        if projection is None:
            return None
        return projection.model_copy(update={"points": []})


class InMemoryProjectionRequestRepository:
    """One live recompute request per tenant; FIFO claim, matching the Postgres adapter (M7.1)."""

    def __init__(self) -> None:
        self.requests: list[ProjectionRequest] = []

    def request(self, tenant_id: str) -> None:
        if any(r.tenant_id == tenant_id for r in self.requests):
            return
        self.requests.append(
            ProjectionRequest(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                requested_at=datetime.now(UTC),
                status="pending",
            )
        )

    def has_pending(self, tenant_id: str) -> bool:
        return any(r.tenant_id == tenant_id for r in self.requests)

    def claim_next(self) -> ProjectionRequest | None:
        for request in self.requests:
            if request.status == "pending":
                request.status = "running"
                return request
        return None

    def complete(self, request_id: str) -> None:
        self.requests = [r for r in self.requests if r.id != request_id]
