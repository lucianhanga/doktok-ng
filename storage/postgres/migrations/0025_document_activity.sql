-- M8: enhanced per-document activity log. Supersedes audit_events with a richer, full-lifecycle
-- model. Deliberately has NO foreign key to documents: activity rows (incl. the "document deleted"
-- event) must SURVIVE the document's cascade delete, so document_id is a plain correlation column
-- and the document identity (filename/title) is snapshotted onto each row.

CREATE TABLE IF NOT EXISTS document_activity (
    id            text PRIMARY KEY,
    tenant_id     text NOT NULL,
    document_id   text,                                   -- correlation only, NOT a foreign key
    job_id        text,
    doc_filename  text,                                   -- identity snapshot @ event time
    doc_title     text,
    phase         text NOT NULL DEFAULT '',               -- lifecycle phase
    event_type    text NOT NULL,
    severity      text NOT NULL DEFAULT 'info',           -- info | warning | error
    record_kind   text,                                   -- metadata|category|entity|ner|record|chunk_embed|feature
    record_id     text,
    actor         text NOT NULL,
    actor_kind    text NOT NULL DEFAULT 'worker',         -- worker | user | system
    description   text NOT NULL DEFAULT '',
    occurred_at   timestamptz NOT NULL DEFAULT now(),
    detail        jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- Primary list + keyset pagination on (occurred_at, id).
CREATE INDEX IF NOT EXISTS idx_activity_tenant_time
    ON document_activity (tenant_id, occurred_at DESC, id DESC);
-- "All activity for one document" (per-document drawer).
CREATE INDEX IF NOT EXISTS idx_activity_tenant_doc_time
    ON document_activity (tenant_id, document_id, occurred_at DESC, id DESC);
-- Errors/warnings are the minority -> a partial index keeps the "needs attention" filter cheap.
CREATE INDEX IF NOT EXISTS idx_activity_tenant_sev_time
    ON document_activity (tenant_id, occurred_at DESC, id DESC)
    WHERE severity <> 'info';

-- Backfill the legacy audit_events trail (severity=info, phase derived from the event_type,
-- identity snapshot from documents where the doc still exists). audit_events is left intact for
-- rollback; new writes go to document_activity.
INSERT INTO document_activity
    (id, tenant_id, document_id, job_id, doc_filename, doc_title,
     phase, event_type, severity, actor, actor_kind, description, occurred_at, detail)
SELECT
    a.id, a.tenant_id, a.document_id, a.job_id, d.original_filename, d.title,
    CASE
        WHEN a.event_type IN ('document.received', 'document.identified', 'document.duplicate')
            THEN 'intake'
        WHEN a.event_type = 'document.activated' THEN 'index'
        WHEN a.event_type IN ('document.failed', 'document.quarantined') THEN 'intake'
        ELSE ''
    END,
    a.event_type,
    CASE
        WHEN a.event_type = 'document.failed' THEN 'error'
        WHEN a.event_type = 'document.quarantined' THEN 'warning'
        ELSE 'info'
    END,
    a.actor, 'worker', '', a.timestamp, a.metadata
FROM audit_events a
LEFT JOIN documents d ON d.id = a.document_id AND d.tenant_id = a.tenant_id
ON CONFLICT (id) DO NOTHING;
