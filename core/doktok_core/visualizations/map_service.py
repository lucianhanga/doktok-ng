"""Assemble the embedding-map payload the Insights tab renders (ADR-0016, M7.1).

Reads a cached projection (geometry only) and resolves each point's color category and text snippet
at read time, so re-classification or re-text never requires re-projecting. The server owns the
category->color palette and legend so the 2D view, 3D view, and legend always agree.
"""

from __future__ import annotations

from doktok_contracts.ports import (
    CategoryRepository,
    ChunkRepository,
    EmbeddingProjectionRepository,
    ProjectionRequestRepository,
)
from doktok_contracts.schemas import (
    EmbeddingMap,
    ProjectionDimStatus,
    ProjectionMeta,
    ProjectionStatus,
    VizLegendEntry,
    VizPoint,
)

from doktok_core.visualizations.service import projection_fingerprint

UNCATEGORIZED = "Uncategorized"
_UNCATEGORIZED_COLOR = "#9ca3af"
_DIMS = (2, 3)

# A fixed, color-blind-friendlier qualitative palette (>= the 20-category tenant cap). Colors are
# assigned by a category's tenant-wide rank, so a category keeps its color across dimensions.
_PALETTE = [
    "#4e79a7",
    "#f28e2b",
    "#59a14f",
    "#e15759",
    "#76b7b2",
    "#edc948",
    "#b07aa1",
    "#ff9da7",
    "#9c755f",
    "#bab0ac",
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
]


def _snippet(text: str, max_chars: int) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= max_chars else collapsed[:max_chars].rstrip() + "…"


class EmbeddingMapService:
    """Builds :class:`EmbeddingMap` / :class:`ProjectionStatus` responses for one tenant."""

    def __init__(
        self,
        projection_repo: EmbeddingProjectionRepository,
        chunk_repo: ChunkRepository,
        category_repo: CategoryRepository,
        request_repo: ProjectionRequestRepository,
        *,
        algorithm: str,
        version: int = 1,
        snippet_chars: int = 160,
    ) -> None:
        self._projections = projection_repo
        self._chunks = chunk_repo
        self._categories = category_repo
        self._requests = request_repo
        self._algorithm = algorithm
        self._version = version
        self._snippet_chars = snippet_chars

    def get_map(self, tenant_id: str, dim: int) -> EmbeddingMap:
        projection = self._projections.get(tenant_id, dim)
        pending = self._requests.has_pending(tenant_id)
        if projection is None:
            return EmbeddingMap(dim=dim, computed=False, recompute_pending=pending)

        doc_ids = list({p.document_id for p in projection.points})
        chunk_ids = [p.chunk_id for p in projection.points]
        primary = self._categories.primary_categories(tenant_id, doc_ids)
        texts = self._chunks.read_texts(tenant_id, chunk_ids)
        color_for = self._palette(tenant_id)

        points = [
            VizPoint(
                chunk_id=p.chunk_id,
                document_id=p.document_id,
                x=p.x,
                y=p.y,
                z=p.z,
                category=primary.get(p.document_id, UNCATEGORIZED),
                cluster=p.cluster,
                snippet=_snippet(texts.get(p.chunk_id, ""), self._snippet_chars),
            )
            for p in projection.points
        ]
        present = {pt.category for pt in points}
        legend = [
            VizLegendEntry(category=name, color=color_for[name])
            for name in color_for
            if name in present
        ]
        meta = ProjectionMeta(
            dim=dim,
            algorithm=projection.algorithm,
            version=projection.version,
            computed_at=projection.computed_at,
            n_points=projection.n_points,
            truncated=projection.truncated,
            stale=projection.input_fingerprint != self._current_fingerprint(tenant_id),
        )
        return EmbeddingMap(
            dim=dim,
            computed=True,
            recompute_pending=pending,
            points=points,
            legend=legend,
            meta=meta,
        )

    def get_status(self, tenant_id: str) -> ProjectionStatus:
        current = self._current_fingerprint(tenant_id)
        dims = []
        for dim in _DIMS:
            header = self._projections.get_header(tenant_id, dim)
            dims.append(
                ProjectionDimStatus(
                    dim=dim,
                    computed=header is not None,
                    stale=header is None or header.input_fingerprint != current,
                    n_points=header.n_points if header else 0,
                    computed_at=header.computed_at if header else None,
                )
            )
        return ProjectionStatus(recompute_pending=self._requests.has_pending(tenant_id), dims=dims)

    def request_recompute(self, tenant_id: str) -> None:
        self._requests.request(tenant_id)

    def _palette(self, tenant_id: str) -> dict[str, str]:
        # Assign by tenant-wide category rank so colors are stable across dims and recomputes.
        ranking = [s.name for s in self._categories.list_summary(tenant_id)]
        color_for = {name: _PALETTE[i % len(_PALETTE)] for i, name in enumerate(ranking)}
        color_for[UNCATEGORIZED] = _UNCATEGORIZED_COLOR
        return color_for

    def _current_fingerprint(self, tenant_id: str) -> str:
        return projection_fingerprint(
            self._chunks.embedding_fingerprint(tenant_id), self._algorithm, self._version
        )
