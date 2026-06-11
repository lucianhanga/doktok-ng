-- M6.2 E1: document enrichment fields (title is reused from the existing column).
ALTER TABLE documents ADD COLUMN IF NOT EXISTS ingested_at timestamptz;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS document_date date;     -- date the doc is about; NULL = n/a
ALTER TABLE documents ADD COLUMN IF NOT EXISTS location text;          -- place the doc refers to; NULL = n/a
ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary text;

-- Filter documents by the date they are about.
CREATE INDEX IF NOT EXISTS idx_documents_tenant_docdate
    ON documents (tenant_id, document_date);
