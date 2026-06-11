-- Review B4: enforce the content-hash dedup that the ingestion pipeline checks in application code.
-- Without a constraint, two files with the same sha256 ingested concurrently (ingest_concurrency>1)
-- can both pass the read-then-write check and both activate. A partial unique index makes "one
-- active document per (tenant, content hash)" a database invariant; non-active rows (failed,
-- duplicate) are exempt so reingest/retry history is unaffected.
CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_active_sha
    ON documents (tenant_id, sha256)
    WHERE status = 'active';
