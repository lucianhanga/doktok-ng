-- M3.6: immutable activity/audit trail (brief section 16, ADR-0006). Append-only by convention.
CREATE TABLE IF NOT EXISTS audit_events (
    id          text PRIMARY KEY,
    tenant_id   text NOT NULL,
    event_type  text NOT NULL,
    actor       text NOT NULL,
    document_id text,
    job_id      text,
    timestamp   timestamptz NOT NULL DEFAULT now(),
    metadata    jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_ts ON audit_events (tenant_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_doc ON audit_events (tenant_id, document_id);
