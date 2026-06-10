-- M5: extracted entities (brief section 16/19). Tenant-scoped.
CREATE TABLE IF NOT EXISTS document_entities (
    id               text PRIMARY KEY,
    tenant_id        text NOT NULL,
    document_id      text NOT NULL,
    version_id       text,
    chunk_id         text,
    entity_text      text NOT NULL,
    entity_type      text NOT NULL,
    normalized_value text NOT NULL,
    frequency        integer NOT NULL DEFAULT 1,
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_entities_tenant_type_value
    ON document_entities (tenant_id, entity_type, normalized_value);
CREATE INDEX IF NOT EXISTS idx_entities_tenant_doc
    ON document_entities (tenant_id, document_id);
