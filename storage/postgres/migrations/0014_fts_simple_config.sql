-- Review B2: the chunk full-text column was hardcoded to the 'english' config, which stems for
-- English only. The corpus is primarily German (German OCR + German keyword configs), so English
-- stemming gave the lexical arm of hybrid retrieval near-zero recall on German terms
-- (e.g. "Rechnung" never matched "Rechnungen"). Switch to the language-agnostic 'simple' config:
-- it lowercases + unicode-normalizes without language-specific stemming, which is neutral across
-- languages and strictly better than 'english' for a multilingual corpus. (Per-document
-- language-aware FTS is a possible future refinement.)
--
-- The tsv column is GENERATED, so it must be dropped and re-added; dropping it also drops the
-- dependent GIN index, which is recreated below.
ALTER TABLE document_chunks DROP COLUMN IF EXISTS tsv;
ALTER TABLE document_chunks
    ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED;
CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON document_chunks USING gin (tsv);
