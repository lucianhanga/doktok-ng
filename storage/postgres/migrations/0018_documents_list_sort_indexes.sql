-- Multi-criteria sorting for the document list (gallery + list views): composite keyset indexes
-- mirroring each new ORDER BY (sort column + id, NULLS LAST) so the range scan + ordering are
-- index-served. "Acquired" (created_at) already has idx_documents_tenant_created_id from 0016.
-- "Category" sort (alphabetically-first active category, a correlated subquery) cannot be index-only
-- and is left to the planner; acceptable at this corpus size, denormalize later if it ever hurts.

-- "Created" = the document's own date (nullable -> NULLS LAST to match the query).
CREATE INDEX IF NOT EXISTS idx_documents_tenant_docdate_id
    ON documents (tenant_id, document_date DESC NULLS LAST, id DESC);

-- "Title" sorted case-insensitively (nullable -> NULLS LAST). ORDER BY uses lower(title) to match.
CREATE INDEX IF NOT EXISTS idx_documents_tenant_title_id
    ON documents (tenant_id, lower(title) NULLS LAST, id DESC);
