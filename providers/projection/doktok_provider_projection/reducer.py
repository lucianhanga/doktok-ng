"""UMAP (preferred) / PCA (fallback) dimensionality reduction for the embedding map (ADR-0016).

Heavy numeric deps (umap-learn, scikit-learn, numpy) are imported lazily inside ``reduce`` so the
package stays importable in environments that do not install the ``engine`` extra (CI, the backend).
UMAP and PCA produce SEPARATE fits per target dimension - a 3D map is not 2D with an added axis.
"""

from __future__ import annotations

from typing import Any


class SklearnUmapReducer:
    """Reduce 1024-dim embeddings to 2D/3D. ``algorithm`` selects the preferred method."""

    def __init__(self, *, algorithm: str = "umap", random_state: int = 42) -> None:
        self.algorithm = algorithm
        self._random_state = random_state

    def reduce(self, vectors: list[list[float]], dim: int) -> list[list[float]]:
        if not vectors:
            return []
        import numpy as np

        matrix = np.asarray(vectors, dtype="float32")
        n_rows, n_features = matrix.shape
        # Degenerate inputs (fewer rows than the target dim, or a single point) cannot support a
        # meaningful fit; place them at the origin so the caller still gets one coord per row.
        if n_rows <= dim:
            return [[0.0] * dim for _ in range(n_rows)]

        coords = self._fit(matrix, dim, n_rows, n_features)
        return [[float(v) for v in row] for row in coords]

    def _fit(self, matrix: Any, dim: int, n_rows: int, n_features: int) -> Any:
        if self.algorithm == "umap" and n_rows >= 10:
            try:
                import umap

                model = umap.UMAP(
                    n_components=dim,
                    n_neighbors=min(15, n_rows - 1),
                    min_dist=0.1,
                    metric="cosine",
                    random_state=self._random_state,
                )
                return model.fit_transform(matrix)
            except Exception:  # noqa: BLE001 - fall back to PCA if UMAP is unavailable or fails
                pass

        from sklearn.decomposition import PCA

        model = PCA(n_components=min(dim, n_rows, n_features), random_state=self._random_state)
        return model.fit_transform(matrix)
