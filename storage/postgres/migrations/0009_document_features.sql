-- M5.10 (ADR-0009): per-document, per-feature processing ledger for reconciliation.
CREATE TABLE IF NOT EXISTS document_features (
    id               text PRIMARY KEY,
    tenant_id        text NOT NULL,
    document_id      text NOT NULL,
    feature          text NOT NULL,
    feature_version  integer NOT NULL DEFAULT 1,
    status           text NOT NULL DEFAULT 'pending',
    attempts         integer NOT NULL DEFAULT 0,
    max_attempts     integer NOT NULL DEFAULT 3,
    last_error       text,
    last_attempt_at  timestamptz,
    completed_at     timestamptz,
    next_attempt_at  timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, document_id, feature)
);

-- Supports the claim query (due rows) and per-document lookups.
CREATE INDEX IF NOT EXISTS idx_docfeatures_claim
    ON document_features (tenant_id, status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_docfeatures_doc
    ON document_features (tenant_id, document_id);
