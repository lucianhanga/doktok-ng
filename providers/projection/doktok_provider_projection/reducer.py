"""Embedding-map projector: PCA pre-reduce -> UMAP (per dim) + HDBSCAN clustering (ADR-0016, M7.2).

Heavy numeric deps (umap-learn, scikit-learn, hdbscan, numpy) are imported lazily inside ``project``
so the package stays importable without the ``engine`` extra (CI, backend). Reduction is two-stage
- PCA 1024D -> 50D denoises and speeds up UMAP - and clustering runs once on the 50D space so the 2D
and 3D maps share cluster ids. UMAP fits each target dim independently (3D is not 2D + an axis).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from doktok_contracts.media import ProjectionResult


class SklearnEmbeddingProjector:
    """Project 1024-dim embeddings to 2D/3D with shared HDBSCAN clusters (umap|pca)."""

    def __init__(
        self,
        *,
        algorithm: str = "umap",
        n_neighbors: int = 15,
        min_cluster_size: int = 8,
        pca_components: int = 50,
        random_state: int = 42,
    ) -> None:
        self.algorithm = algorithm
        self._n_neighbors = n_neighbors
        self._min_cluster_size = max(2, min_cluster_size)
        self._pca_components = max(2, pca_components)
        self._random_state = random_state

    def project(self, vectors: list[list[float]], dims: Sequence[int]) -> ProjectionResult:
        dim_list = [int(d) for d in dims]
        if not vectors:
            return ProjectionResult(coords={d: [] for d in dim_list}, clusters=[])
        import numpy as np

        matrix = np.asarray(vectors, dtype="float32")
        n_rows, n_features = matrix.shape
        reduced = self._pca(matrix, n_rows, n_features)
        clusters = self._cluster(reduced, n_rows)
        coords = {d: self._embed(reduced, d, n_rows) for d in dim_list}
        return ProjectionResult(coords=coords, clusters=clusters)

    def prewarm(self) -> None:
        # A tiny fit triggers the UMAP + HDBSCAN numba JIT, off the first real recompute.
        try:
            import numpy as np

            sample = np.random.default_rng(0).random((30, self._pca_components)).tolist()
            self.project(sample, (2, 3))
        except Exception:  # noqa: BLE001 - pre-warming is best-effort; never fail startup over it
            pass

    def _pca(self, matrix: Any, n_rows: int, n_features: int) -> Any:
        if n_features <= self._pca_components or n_rows <= self._pca_components:
            return matrix
        from sklearn.decomposition import PCA

        return PCA(
            n_components=self._pca_components, random_state=self._random_state
        ).fit_transform(matrix)

    def _cluster(self, reduced: Any, n_rows: int) -> list[int]:
        if n_rows < max(self._min_cluster_size, 3):
            return [-1] * n_rows
        try:
            import hdbscan

            labels = hdbscan.HDBSCAN(
                min_cluster_size=self._min_cluster_size, metric="euclidean"
            ).fit_predict(reduced)
            return [int(v) for v in labels]
        except Exception:  # noqa: BLE001 - clustering is additive; fall back to "all noise" if absent
            return [-1] * n_rows

    def _embed(self, reduced: Any, dim: int, n_rows: int) -> list[list[float]]:
        if n_rows <= dim:
            return [[0.0] * dim for _ in range(n_rows)]
        coords = self._fit(reduced, dim, n_rows)
        return [[float(v) for v in row] for row in coords]

    def _fit(self, reduced: Any, dim: int, n_rows: int) -> Any:
        if self.algorithm == "umap" and n_rows >= 10:
            try:
                import umap

                model = umap.UMAP(
                    n_components=dim,
                    n_neighbors=min(self._n_neighbors, n_rows - 1),
                    min_dist=0.1,
                    metric="cosine",
                    random_state=self._random_state,
                )
                return model.fit_transform(reduced)
            except Exception:  # noqa: BLE001 - fall back to PCA if UMAP is unavailable or fails
                pass

        from sklearn.decomposition import PCA

        return PCA(n_components=min(dim, n_rows), random_state=self._random_state).fit_transform(
            reduced
        )
