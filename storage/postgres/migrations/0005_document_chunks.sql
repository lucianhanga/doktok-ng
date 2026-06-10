-- M4: document chunks for hybrid retrieval (brief section 16, ADR-0005).
-- Ensure pgvector exists (the CI Postgres service does not run the compose initdb script).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS document_chunks (
    id           text PRIMARY KEY,
    tenant_id    text NOT NULL,
    document_id  text NOT NULL,
    version_id   text,
    page_start   integer,
    page_end     integer,
    heading_path jsonb NOT NULL DEFAULT '[]'::jsonb,
    text         text NOT NULL,
    token_count  integer,
    embedding    vector(1024),
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    metadata     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chunks_tenant_doc ON document_chunks (tenant_id, document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON document_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops);
