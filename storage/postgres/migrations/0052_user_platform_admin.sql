-- #613 (security audit F-01, ADR-0025): platform-owner tier.
--
-- Platform admin is a DEPLOYMENT-level property of a user (not a tenant role): only platform admins
-- may reach deployment-spanning surfaces (portable backup export/restore, DRP, egress toggle,
-- tenant provisioning). Static env-map tokens and host tooling are platform admins by construction
-- (see core/security/auth.py); this flag is the DB-backed identity half of that model. Additive +
-- idempotent; existing users default to false, which keeps them tenant admins only.
--
-- Rollback: ALTER TABLE users DROP COLUMN IF EXISTS is_platform_admin;

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_platform_admin boolean NOT NULL DEFAULT false;
