-- #558 (EPIC #523): per-user server-side preference store, so UI preferences (documents-list
-- layout, thumbnail size, chat mode/reasoning, insights sub-tab, ...) sync across devices instead
-- of living in per-browser localStorage. Additive + idempotent.
--
-- Keyed by (tenant_id, subject, key): ``subject`` is the authenticated user's id, or - for a
-- tenant-scoped (login-less) static-token caller - the tenant id, so a local-first single-operator
-- deployment still gets one persistent per-tenant preference bucket with no login (see
-- actor_identity in core/audit/logger.py). ``value`` is arbitrary JSON.
--
-- Rollback: DROP TABLE user_preferences;

CREATE TABLE IF NOT EXISTS user_preferences (
    tenant_id  text NOT NULL,
    subject    text NOT NULL,
    key        text NOT NULL,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subject, key)
);
