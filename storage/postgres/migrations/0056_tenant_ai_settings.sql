-- #709 (epic #708, T1): per-tenant model-stack overrides.
--
-- One row per tenant holding a PARTIAL AiSettings document (TenantAiSettings: any purpose may be
-- null = inherit). Resolution per purpose: tenant override -> global app_settings.ai_settings ->
-- env defaults. Embedding + OCR stay deployment-global and are NOT stored here.
--
-- Rollback: DROP TABLE IF EXISTS tenant_ai_settings;

CREATE TABLE IF NOT EXISTS tenant_ai_settings (
    tenant_id   text PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    value       jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
