"""SklearnEmbeddingProjector tests. Degenerate cases run anywhere; real fits need ``engine``."""

from __future__ import annotations

import importlib.util

import pytest
from doktok_provider_projection import SklearnEmbeddingProjector

_HAS_ENGINE = importlib.util.find_spec("numpy") is not None and (
    importlib.util.find_spec("sklearn") is not None or importlib.util.find_spec("umap") is not None
)
_HAS_HDBSCAN = importlib.util.find_spec("hdbscan") is not None
_needs_engine = pytest.mark.skipif(not _HAS_ENGINE, reason="numpy + sklearn/umap not installed")


def test_empty_input_returns_empty() -> None:
    result = SklearnEmbeddingProjector().project([], (2, 3))
    assert result.coords == {2: [], 3: []} and result.clusters == []


@_needs_engine
def test_too_few_points_land_at_the_origin() -> None:
    # Fewer rows than the target dim cannot support a fit; each row gets a dim-length origin coord.
    result = SklearnEmbeddingProjector().project([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], (3,))
    assert result.coords[3] == [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]


@_needs_engine
def test_projects_to_2d_and_3d_with_one_cluster_per_point() -> None:
    # Two well-separated blobs in a 60-dim space so PCA pre-reduction (->50) actually runs.
    vectors = [[float(i % 30 < 15) * 50 + (i % 4)] * 60 for i in range(40)]
    # Prefer PCA so the test is fast/deterministic without UMAP installed.
    projector = SklearnEmbeddingProjector(algorithm="pca", min_cluster_size=4)

    result = projector.project(vectors, (2, 3))

    assert len(result.coords[2]) == 40 and all(len(c) == 2 for c in result.coords[2])
    assert len(result.coords[3]) == 40 and all(len(c) == 3 for c in result.coords[3])
    # One cluster id per vector; shared across dims (clustered once on the PCA space).
    assert len(result.clusters) == 40
    if _HAS_HDBSCAN:
        # With two separated blobs, HDBSCAN should find at least one real (non-noise) cluster.
        assert any(c >= 0 for c in result.clusters)
    else:
        assert all(c == -1 for c in result.clusters)


def test_prewarm_is_safe_without_engine() -> None:
    # Pre-warm must never raise, even when the numeric deps are absent.
    SklearnEmbeddingProjector().prewarm()
