-- M7.3: structured "unidentifiable" marker for documents (ADR-0017). The enrichment extractor will
-- set this TRUE/FALSE explicitly in Phase 2; NULL = not yet assessed (pre-migration / pre-enrichment).
-- The boolean is the queryable source of truth for filtering; any numeric confidence lives in
-- documents.metadata, not here.
--
-- Nullable, NO default: a pure catalog change - no table rewrite or row scan even on a large table,
-- so the ~2000+ existing rows stay valid instantly and nothing is re-ingested.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS unidentifiable boolean;

-- "Show only unidentifiable" cheaply, tenant-scoped. Partial index indexes only the TRUE minority.
CREATE INDEX IF NOT EXISTS idx_documents_tenant_unidentifiable
    ON documents (tenant_id, id)
    WHERE unidentifiable IS TRUE;

-- One-time backfill of today's heuristic (the LLM titled meaningless scans "Unidentifiable
-- Document") into the structured marker. Idempotent: the IS NULL guard means a re-run only ever sets
-- the same rows, and never overrides a value the extractor later corrected. Content-based, so it is
-- correct across every tenant without a per-tenant loop.
UPDATE documents
   SET unidentifiable = TRUE
 WHERE unidentifiable IS NULL
   AND status = 'active'
   AND title = 'Unidentifiable Document';
