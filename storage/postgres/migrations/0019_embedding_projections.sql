-- M7.1: cached 2D/3D projections of the embedding space for the Insights tab (ADR-0016).
-- A projection is a tenant-level aggregate (UMAP/PCA fits all of a tenant's chunks jointly), cached
-- so the map is stable across sessions and cheap to read. One cached projection per (tenant, dim);
-- recompute replaces it. Staleness is detected by comparing input_fingerprint, not by row mutation.

CREATE TABLE IF NOT EXISTS embedding_projections (
    id                text PRIMARY KEY,
    tenant_id         text NOT NULL,
    dim               smallint NOT NULL,
    algorithm         text NOT NULL,
    version           integer NOT NULL DEFAULT 1,
    input_fingerprint text NOT NULL,
    n_points          integer NOT NULL,
    truncated         boolean NOT NULL DEFAULT false,
    computed_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT embedding_projections_tenant_dim_key UNIQUE (tenant_id, dim),
    CONSTRAINT embedding_projections_dim_check CHECK (dim IN (2, 3))
);

-- Points are a snapshot of where each chunk landed. They reference the projection header (cascade on
-- replace), NOT document_chunks: re-embedding deletes/recreates chunks, and we want the snapshot to
-- survive until an explicit recompute (the fingerprint flags it stale meanwhile).
CREATE TABLE IF NOT EXISTS embedding_projection_points (
    projection_id text NOT NULL REFERENCES embedding_projections(id) ON DELETE CASCADE,
    tenant_id     text NOT NULL,
    chunk_id      text NOT NULL,
    document_id   text NOT NULL,
    x             real NOT NULL,
    y             real NOT NULL,
    z             real,
    PRIMARY KEY (projection_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_projection_points_projection
    ON embedding_projection_points (projection_id);
