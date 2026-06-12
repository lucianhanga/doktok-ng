# ADR-0016: Insights tab and embedding-space visualization

## Status

Accepted

A new top-level **Insights** tab is added to the web UI; its first sub-tab, **Embedding Space**,
plots the RAG chunk embeddings projected to 2D and 3D, colored by document category. This ADR records
the design decided from the UI/UX research (2026-06-12) and the architectural choices that follow
from this project's local-first, no-egress, multi-tenant constraints.

## Context

The system stores one 1024-dimensional embedding per chunk (`document_chunks.embedding`, pgvector,
model `qwen3-embedding:0.6b`, migration `0005_document_chunks.sql`), tenant-scoped. Documents carry
**categories** assigned by the classifier feature (`DocClassifyFeature`). Until now the embedding
space has only been used internally for retrieval; there is no way to *see* it.

The request: a Data Visualizations surface whose first view projects the embedding space to 2D/3D and
colors each point by the category of its parent document, to make clustering, category separation, and
outliers visible.

Two data-model facts shape the design:

- **Documents are multi-label.** There is no scalar `category` column; a document links to up to 5
  categories, with a tenant cap of 20 active categories (migration `0011_categories.sql`). A point
  therefore needs a deterministic *single* color.
- **A projection is a tenant-level aggregate.** UMAP/t-SNE fit *all* of a tenant's chunks jointly; the
  result is meaningless per-document. This does **not** fit the per-document `FeatureReconciler`
  (ADR-0009), which is the model used for every other derived feature.

## Decision

### 1. Dimensionality reduction runs in the backend, precomputed and cached

1024-dim → 2D/3D reduction is computed **server-side** and cached, never in the browser, and never
via a hosted service (honours `DOKTOK_NO_EGRESS`). Rationale:

- Local-first / no-egress forbids hosted projection APIs; the backend is Python, where
  `umap-learn` / `scikit-learn` are mature.
- A **stable** map across page loads and sessions matters for interpretation; caching a fit beats
  recomputing a different layout on every visit.

- **Algorithm:** **UMAP** preferred (`umap-learn`), **PCA** (`scikit-learn`) as a deterministic
  fallback / fast path. These dependencies are **not yet in `pyproject.toml`** and are added by the
  worker/projection ticket.
- **2D and 3D are separate fits** — the 3D layout is not the 2D layout with a dropped Z axis; each
  target dimensionality is reduced independently.
- **Recompute is on-demand**, never silent/automatic. The cached projection records an
  `input_fingerprint` (e.g. over tenant chunk count + max `updated_at` + algorithm + version); when
  the live fingerprint differs, the UI shows a **stale** state with a recompute action.

### 2. Projection is a dedicated tenant-aggregate job, not a reconciler feature

Because a projection fits all chunks at once, it is implemented as a **separate worker job** keyed by
`(tenant_id, dim, version)`, not as a `FeatureProcessor`. It reads the tenant's chunk embeddings,
fits the reducer, and writes the cached points. It is triggered on demand (API) and may later be
scheduled; it is **not** part of per-document ingestion.

### 3. Coloring by a deterministic primary category, palette owned by the server

Each point is colored by the **primary category** of its parent document, resolved deterministically:
the linked category with the highest tenant-wide document count, name as tiebreak. Documents with no
category fall into an **"Uncategorized"** bucket (neutral gray, distinct marker for accessibility).
The **server owns the category → color palette and the legend**, returned alongside the points, so the
2D view, 3D view, and legend always agree and colors are stable across recomputes.

### 4. Rendering with deck.gl, one library for both dimensions

The UI renders with **deck.gl `ScatterplotLayer`** — `OrthographicView` for 2D, `OrbitView` for 3D —
a single library across both dimensions with GPU picking for hover/click, scaling from today's
thousands of points toward tens of thousands. **Plotly** is an acceptable stopgap at the current small
scale. The sub-tab is **lazy-loaded** so the visualization libraries never bloat the main bundle.

### 5. Surface and naming

A new top-level **Insights** tab, first sub-tab **Embedding Space**. The sub-tab mirrors
`DocumentsPanel` conventions: radiogroup sub-tab nav, control bar (2D/3D toggle, point size/opacity,
recompute), polled status, and `localStorage`-persisted view preferences.

### 6. API contract (tenant-scoped)

- `GET /api/v1/visualizations/embeddings?dim=2|3` → `{ points: [{ x, y, z?, category, document_id,
  chunk_id, snippet }], legend: [{ category, color }], projection: { algorithm, dim, version,
  computed_at, input_fingerprint, n_points, truncated } }`.
- `GET /api/v1/visualizations/embeddings/status` → current cache state per dim + live fingerprint
  (so the UI can show fresh / stale / not-computed).
- `POST /api/v1/visualizations/embeddings/recompute` → enqueue the projection job (2D + 3D).

All endpoints are scoped by `tenant_id` from the bearer token; a tenant only ever sees its own chunks.

## Consequences

- **New Python deps** (`umap-learn`, `scikit-learn`) increase the worker image; PCA-only is the
  lighter fallback if UMAP proves heavy.
- **A new persistence object** (projection cache) is needed — keyed by `(tenant_id, dim, version)`,
  holding points + fingerprint + `computed_at`. The database-architect decides table vs. JSONB blob
  and indexing.
- **A new job type** outside the reconciler is introduced; the worker must host and trigger it. This
  is the first tenant-aggregate background job and sets the pattern for future ones.
- **Eventual/stale by design:** the map reflects the last computed fit, not live state; staleness is
  surfaced, not hidden. Recompute cost grows with chunk count (UMAP is the expensive part).
- **Multi-label color loss:** coloring by a single primary category discards secondary labels; the
  legend documents the rule. Later phases may expose per-label filtering.

## Rollout / phasing

1. **MVP (this milestone, M7.1):** projection cache + on-demand UMAP/PCA job (2D + 3D) → read/status/
   recompute endpoints with server-owned palette → Insights tab + Embedding Space sub-tab rendering
   the scatter with 2D/3D toggle, legend (per-category show/hide), hover tooltip (snippet + source
   document), click-to-open-document, and the full set of API-driven states (loading, empty, not-yet-
   computed, stale, error).
2. **Later:** lasso/box selection of chunks, filter by document/token/category, density or cluster
   overlays, and projecting a live search query onto the existing map.

## Hand-offs

- **database-architect:** projection cache schema + migration (storage of points, `input_fingerprint`,
  `computed_at`, keyed by `(tenant_id, dim, version)`), retention/recompute semantics.
- **backend-api-architect:** the three endpoints + contracts, primary-category resolution, server-owned
  palette/legend, tenant scoping, truncation policy for very large tenants.
- **worker / agentic side:** the tenant-aggregate projection job (UMAP/PCA), dependency additions,
  on-demand trigger, fingerprint computation.
- **ui-developer:** the Insights tab + Embedding Space sub-tab per the UX spec, deck.gl integration,
  lazy-loading, all API-driven states.
