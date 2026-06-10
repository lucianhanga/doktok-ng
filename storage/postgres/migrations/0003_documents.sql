-- M2: documents (brief section 16). Tenant-scoped (ADR-0007). Versions/pages/chunks arrive later.
CREATE TABLE IF NOT EXISTS documents (
    id                 text PRIMARY KEY,
    tenant_id          text NOT NULL,
    current_version_id text,
    sha256             text NOT NULL,
    original_filename  text NOT NULL,
    detected_mime      text,
    title              text,
    status             text NOT NULL,
    storage_path       text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    activated_at       timestamptz,
    metadata           jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant_created_at
    ON documents (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_tenant_sha256
    ON documents (tenant_id, sha256);
