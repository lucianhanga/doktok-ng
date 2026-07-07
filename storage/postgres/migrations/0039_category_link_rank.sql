-- Issue #496: fix embedding-map collapsing to one category.
-- The classifier's intended primary is now the first label it emits; persist that ordering as a
-- rank column (0 = primary, ascending = less relevant) so the read path can honour it rather than
-- falling back to the tenant-wide document count, which caused the most-common label to win for
-- every document.
-- Additive + idempotent; existing rows default to rank 0 (treated as primary on read, with name
-- as tiebreak, so old data stays deterministic without a full re-classification pass).
ALTER TABLE document_category_links
    ADD COLUMN IF NOT EXISTS rank integer NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_doc_cat_links_rank
    ON document_category_links (tenant_id, document_id, rank);
