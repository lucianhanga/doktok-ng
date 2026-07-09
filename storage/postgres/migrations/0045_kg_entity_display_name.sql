-- Entity rename via a display-name override. The node id is derived from the normalized value and
-- every edge references that id, so a true rename would orphan the graph. Instead a nullable
-- ``display_name`` overrides only the SHOWN label (e.g. to fix an OCR'd name) while the id, the
-- normalized value, mentions and edges stay untouched. NULL = show the normalized value.
-- Additive + idempotent. Rollback: ALTER TABLE kg_entities DROP COLUMN display_name;

ALTER TABLE kg_entities ADD COLUMN IF NOT EXISTS display_name text;
