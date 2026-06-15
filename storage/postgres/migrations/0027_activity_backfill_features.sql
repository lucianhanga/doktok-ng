-- M8: one-time backfill of enrichment activity from the document_features ledger. The reconciler
-- now logs a feature.completed row as each feature runs (PR2), but documents enriched before that
-- code shipped have no such trail. This synthesises one feature.completed activity row per already-
-- "done" feature so the historical enrichment pipeline is visible in the Activity table too.
--
-- Idempotent: the activity id is derived from the feature-row id ('feat-' || id), and live events
-- use random uuids, so re-running or future live runs never collide. Mirrors the reconciler's
-- description ("<feature> completed"), phase (enrich) and record_kind/record_id (feature).
INSERT INTO document_activity
    (id, tenant_id, document_id, job_id, doc_filename, doc_title,
     phase, event_type, severity, record_kind, record_id, actor, actor_kind, description,
     occurred_at, detail)
SELECT
    'feat-' || f.id,
    f.tenant_id,
    f.document_id,
    NULL,
    d.original_filename,
    d.title,
    'enrich',
    'feature.completed',
    'info',
    'feature',
    f.feature,
    'reconciler',
    'worker',
    f.feature || ' completed',
    COALESCE(f.completed_at, f.updated_at, now()),
    jsonb_build_object('feature', f.feature, 'version', f.feature_version, 'backfilled', true)
FROM document_features f
LEFT JOIN documents d ON d.id = f.document_id AND d.tenant_id = f.tenant_id
WHERE f.status = 'done'
ON CONFLICT (id) DO NOTHING;
