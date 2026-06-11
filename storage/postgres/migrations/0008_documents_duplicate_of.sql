-- M5.9: link a duplicate document record to the already-ingested original.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS duplicate_of text;

CREATE INDEX IF NOT EXISTS idx_documents_tenant_status
    ON documents (tenant_id, status);
