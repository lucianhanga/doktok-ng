-- #554 (EPIC #523): DB-backed tenant + user registry, replacing the static DOKTOK_TENANT_TOKENS
-- map behind the ADR-0008 resolution seam. Additive + idempotent.
--
-- The static env map remains a FALLBACK for local-first/dev (see core/security/auth.py); a DB token
-- takes precedence when it matches, so existing single-tenant deployments keep working with no DB
-- rows. Auth/login (#555) and RBAC (#556) build on this registry; this migration is the data model
-- only.
--
-- Tables:
--   tenants     - one row per tenant (the unit of data isolation, ADR-0007).
--   users       - registry of users within a tenant. Credentials/sessions are #555; this is the
--                 registry so other rows (api_tokens, future audit actor) can reference a user.
--   api_tokens  - hashed, revocable bearer tokens resolving to a tenant (+ optional user). The
--                 plaintext token is NEVER stored - only its sha256 (token_sha256, UNIQUE for O(1)
--                 lookup). token_prefix keeps the first few chars for display/identification only.
--
-- Rollback: DROP TABLE api_tokens; DROP TABLE users; DROP TABLE tenants;

CREATE TABLE IF NOT EXISTS tenants (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    status      text NOT NULL DEFAULT 'active',   -- active | suspended
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id           text PRIMARY KEY,
    tenant_id    text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email        text NOT NULL,
    display_name text NOT NULL DEFAULT '',
    status       text NOT NULL DEFAULT 'active',   -- active | deactivated
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email)
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id            text PRIMARY KEY,
    tenant_id     text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id       text REFERENCES users(id) ON DELETE CASCADE,
    token_sha256  text NOT NULL UNIQUE,
    token_prefix  text NOT NULL DEFAULT '',
    name          text NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz
);

CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_api_tokens_tenant ON api_tokens(tenant_id);
