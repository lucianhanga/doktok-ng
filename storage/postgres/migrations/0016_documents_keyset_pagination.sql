-- Review follow-up: keyset (cursor) pagination + a "needs attention" filter for the document list.
-- Keyset paginates on the total sort key (created_at DESC, id DESC) - created_at is not unique under
-- bulk ingest, so id is the tiebreaker. This composite index serves the range scan; the older
-- (tenant_id, created_at DESC) index becomes redundant for the list and is dropped.
CREATE INDEX IF NOT EXISTS idx_documents_tenant_created_id
    ON documents (tenant_id, created_at DESC, id DESC);
DROP INDEX IF EXISTS idx_documents_tenant_created_at;

-- "Needs attention" = a document with at least one non-done feature. A partial index keeps the
-- EXISTS probe tiny (only the minority of not-done rows) and also speeds the stats pending count.
CREATE INDEX IF NOT EXISTS idx_docfeatures_attention
    ON document_features (tenant_id, document_id)
    WHERE status <> 'done';
