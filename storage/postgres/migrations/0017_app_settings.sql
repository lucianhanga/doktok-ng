-- Settings tab: persisted, global system configuration (not tenant-scoped) - currently the AI model
-- choices per purpose. A simple key -> JSON document store; values are merged over the env defaults
-- at startup (changes take effect on restart).
CREATE TABLE IF NOT EXISTS app_settings (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
