-- Per-document processing telemetry: persist per-feature run metrics (duration + enrichment token
-- counts + model) so the detail view can show how long each step took and how many tokens it spent.
-- Additive + idempotent; jsonb keeps the evolving shape out of the schema. Old rows default to '{}'
-- and degrade to nulls/zeros in the read-side ProcessingTelemetry. No backfill.
ALTER TABLE document_features
    ADD COLUMN IF NOT EXISTS metrics jsonb NOT NULL DEFAULT '{}'::jsonb;
