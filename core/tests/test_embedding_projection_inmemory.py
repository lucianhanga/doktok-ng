"""In-memory embedding-projection cache (ADR-0016, M7.1)."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import EmbeddingProjection, ProjectionPoint
from doktok_core.visualizations.inmemory import InMemoryEmbeddingProjectionRepository


def _proj(dim: int, fingerprint: str) -> EmbeddingProjection:
    return EmbeddingProjection(
        tenant_id="t1",
        dim=dim,
        algorithm="umap",
        version=1,
        input_fingerprint=fingerprint,
        n_points=1,
        computed_at=datetime.now(UTC),
        points=[ProjectionPoint(chunk_id="c0", document_id="d0", x=1.0, y=2.0)],
    )


def test_upsert_replaces_and_get_returns_points() -> None:
    repo = InMemoryEmbeddingProjectionRepository()
    assert repo.get("t1", 2) is None

    repo.upsert(_proj(2, "fp-old"))
    repo.upsert(_proj(2, "fp-new"))

    got = repo.get("t1", 2)
    assert got is not None and got.input_fingerprint == "fp-new" and len(got.points) == 1


def test_get_header_drops_points() -> None:
    repo = InMemoryEmbeddingProjectionRepository()
    repo.upsert(_proj(3, "fp"))

    header = repo.get_header("t1", 3)
    assert header is not None and header.points == [] and header.n_points == 1
    # The stored projection still has its points (header is a copy).
    assert len(repo.get("t1", 3).points) == 1  # type: ignore[union-attr]
