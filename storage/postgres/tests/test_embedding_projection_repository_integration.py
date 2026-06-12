"""Integration tests for the embedding-projection cache (ADR-0016, M7.1; test* tenants only)."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import EmbeddingProjection, ProjectionPoint
from doktok_storage_postgres import Database, PostgresEmbeddingProjectionRepository

TENANT = "test-a"


def _projection(
    dim: int, *, n: int, fingerprint: str, algorithm: str = "umap"
) -> EmbeddingProjection:
    points = [
        ProjectionPoint(
            chunk_id=f"c{i}",
            document_id=f"d{i % 3}",
            x=float(i),
            y=float(-i),
            z=float(i * 2) if dim == 3 else None,
        )
        for i in range(n)
    ]
    return EmbeddingProjection(
        tenant_id=TENANT,
        dim=dim,
        algorithm=algorithm,
        version=1,
        input_fingerprint=fingerprint,
        n_points=n,
        truncated=False,
        computed_at=datetime.now(UTC),
        points=points,
    )


def test_upsert_then_get_round_trips_points(db: Database) -> None:
    repo = PostgresEmbeddingProjectionRepository(db)
    repo.upsert(_projection(2, n=5, fingerprint="fp-1"))

    got = repo.get(TENANT, 2)
    assert got is not None
    assert got.algorithm == "umap" and got.n_points == 5 and got.input_fingerprint == "fp-1"
    assert len(got.points) == 5
    assert all(p.z is None for p in got.points)
    # 3D round-trips its z coordinate.
    repo.upsert(_projection(3, n=4, fingerprint="fp-3d"))
    got3 = repo.get(TENANT, 3)
    assert got3 is not None and all(p.z is not None for p in got3.points)


def test_upsert_replaces_the_existing_projection_for_a_dim(db: Database) -> None:
    repo = PostgresEmbeddingProjectionRepository(db)
    repo.upsert(_projection(2, n=5, fingerprint="fp-old"))
    repo.upsert(_projection(2, n=2, fingerprint="fp-new", algorithm="pca"))

    got = repo.get(TENANT, 2)
    assert got is not None
    assert got.input_fingerprint == "fp-new" and got.algorithm == "pca"
    assert got.n_points == 2 and len(got.points) == 2  # old points were cascaded away


def test_get_header_omits_points_but_keeps_metadata(db: Database) -> None:
    repo = PostgresEmbeddingProjectionRepository(db)
    repo.upsert(_projection(2, n=7, fingerprint="fp-h"))

    header = repo.get_header(TENANT, 2)
    assert header is not None
    assert header.n_points == 7 and header.input_fingerprint == "fp-h"
    assert header.points == []  # status checks do not load all points


def test_get_returns_none_when_not_computed(db: Database) -> None:
    repo = PostgresEmbeddingProjectionRepository(db)
    assert repo.get(TENANT, 2) is None
    assert repo.get_header(TENANT, 3) is None


def test_projections_are_tenant_isolated(db: Database) -> None:
    repo = PostgresEmbeddingProjectionRepository(db)
    repo.upsert(_projection(2, n=3, fingerprint="fp-a"))
    other = EmbeddingProjection(
        tenant_id="test-b",
        dim=2,
        algorithm="umap",
        version=1,
        input_fingerprint="fp-b",
        n_points=0,
        computed_at=datetime.now(UTC),
        points=[],
    )
    repo.upsert(other)

    assert repo.get(TENANT, 2).input_fingerprint == "fp-a"  # type: ignore[union-attr]
    assert repo.get("test-b", 2).input_fingerprint == "fp-b"  # type: ignore[union-attr]
