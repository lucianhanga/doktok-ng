-- Records detail v1: make extracted_records.confidence honest. The extractor never emits a real
-- score today, so the original NOT NULL DEFAULT 1.0 made every UNSCORED row read as "100%
-- confident". Drop the default + NOT NULL so new rows stay NULL until a model genuinely scores
-- them, and backfill the existing 1.0 rows (which were never actually scored) to NULL.
-- Additive + idempotent: re-running drops an already-absent default/constraint without error, and
-- after the backfill no 1.0 rows remain so the UPDATE becomes a no-op.
ALTER TABLE extracted_records ALTER COLUMN confidence DROP DEFAULT;
ALTER TABLE extracted_records ALTER COLUMN confidence DROP NOT NULL;
UPDATE extracted_records SET confidence = NULL WHERE confidence = 1.0;
