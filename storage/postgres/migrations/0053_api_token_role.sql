-- #645 (security audit F-33): least-privilege machine tokens.
--
-- api_tokens.role scopes what a USER-LESS (machine) token may do: viewer | editor | admin,
-- enforced at credential resolution (dependencies.resolve_caller_role). Previously every
-- tenant-scoped credential without a user resolved to full admin, so a leaked script token was a
-- tenant-wide compromise. Existing rows keep 'admin' (their effective behavior today); newly
-- minted tokens default to viewer at the API. Static host-provisioned tokens (DOKTOK_TENANT_TOKENS)
-- and user-bound tokens are unaffected (the platform tier and the user's own role, respectively).
--
-- Rollback: ALTER TABLE api_tokens DROP COLUMN IF EXISTS role;

ALTER TABLE api_tokens ADD COLUMN IF NOT EXISTS role text NOT NULL DEFAULT 'admin';
