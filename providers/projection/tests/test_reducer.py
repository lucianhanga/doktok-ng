"""SklearnUmapReducer tests. Degenerate cases run anywhere; real fits need the ``engine`` extra."""

from __future__ import annotations

import importlib.util

import pytest
from doktok_provider_projection import SklearnUmapReducer

_HAS_ENGINE = importlib.util.find_spec("numpy") is not None and (
    importlib.util.find_spec("sklearn") is not None or importlib.util.find_spec("umap") is not None
)
_needs_engine = pytest.mark.skipif(not _HAS_ENGINE, reason="numpy + sklearn/umap not installed")


def test_empty_input_returns_empty() -> None:
    assert SklearnUmapReducer().reduce([], 2) == []


@_needs_engine
def test_too_few_points_land_at_the_origin() -> None:
    # Fewer rows than the target dim cannot support a fit; each row gets a dim-length origin coord.
    coords = SklearnUmapReducer().reduce([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], 3)
    assert coords == [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]


@_needs_engine
def test_reduces_to_requested_dimension() -> None:
    vectors = [[float(i), float(i % 3), float(i % 5), 1.0] for i in range(30)]
    # Prefer PCA so the test is fast and deterministic without UMAP installed.
    reducer = SklearnUmapReducer(algorithm="pca")

    coords2 = reducer.reduce(vectors, 2)
    coords3 = reducer.reduce(vectors, 3)

    assert len(coords2) == 30 and all(len(c) == 2 for c in coords2)
    assert len(coords3) == 30 and all(len(c) == 3 for c in coords3)
