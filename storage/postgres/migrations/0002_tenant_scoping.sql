-- M1.5: multi-tenancy (ADR-0007). Add a tenant discriminator to ingestion_jobs.
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL DEFAULT 'default';

-- Tenant-leading composite indexes for per-tenant dedup and listing.
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_tenant_sha256
    ON ingestion_jobs (tenant_id, sha256);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_tenant_created_at
    ON ingestion_jobs (tenant_id, created_at DESC);
