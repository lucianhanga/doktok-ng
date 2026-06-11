-- M6.3: structured records extracted from documents, for deterministic aggregation queries
-- (e.g. "total spent at Block House across all statements") that top-k RAG cannot answer.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS extracted_records (
    id              text PRIMARY KEY,
    tenant_id       text NOT NULL,
    document_id     text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    record_type     text NOT NULL,            -- 'card_transaction' (more types later)
    feature_version int  NOT NULL DEFAULT 1,

    -- provenance: the exact source line, for auditable answers
    source_page     int,
    raw_text        text NOT NULL,

    -- typed aggregation spine
    occurred_on         date,                 -- the transaction date
    amount_minor        bigint,               -- INTEGER minor units (cents); never float/money type
    currency            char(3),              -- ISO 4217
    direction           text,                 -- 'debit' (spend) | 'credit' (refund/payment)
    merchant_raw        text,                 -- "BLOCK HOUSE RESTAURANT HAMBURG"
    merchant_normalized text,                 -- "block house restaurant hamburg" (fuzzy-match key)
    description         text,
    account_label       text,
    confidence          real NOT NULL DEFAULT 1.0,

    extras          jsonb NOT NULL DEFAULT '{}'::jsonb,  -- doctype-specific long tail
    created_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT extracted_records_money_currency
        CHECK (amount_minor IS NULL OR currency IS NOT NULL)
);

-- Fuzzy merchant matching ("Block House" inside "BLOCKHOUSE RESTAURANT #42 HAMBURG").
CREATE INDEX IF NOT EXISTS idx_records_merchant_trgm
    ON extracted_records USING gin (merchant_normalized gin_trgm_ops);
-- Filter by type + date range, and aggregate.
CREATE INDEX IF NOT EXISTS idx_records_tenant_type_date
    ON extracted_records (tenant_id, record_type, occurred_on);
-- Idempotent re-extraction (delete this document's records).
CREATE INDEX IF NOT EXISTS idx_records_tenant_doc
    ON extracted_records (tenant_id, document_id);
