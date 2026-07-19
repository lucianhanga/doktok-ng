-- #649 (security audit F-37): case-insensitive email uniqueness per tenant.
--
-- Login resolves users with lower(email)=lower(%s), but UNIQUE(tenant_id, email) is
-- case-SENSITIVE: two concurrent create/invite calls with case-variant emails (A@x.com vs
-- a@x.com) both passed the case-insensitive pre-check and both inserted, leaving login to verify
-- against an arbitrary duplicate row. This expression index closes the race at the DB level.
--
-- Rollback: DROP INDEX IF EXISTS users_tenant_email_lower_uniq;

CREATE UNIQUE INDEX IF NOT EXISTS users_tenant_email_lower_uniq ON users (tenant_id, lower(email));
