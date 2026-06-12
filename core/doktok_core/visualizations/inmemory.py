"""In-memory embedding-projection cache for tests and local/dev runs (ADR-0016, M7.1)."""

from __future__ import annotations

from doktok_contracts.schemas import EmbeddingProjection


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
