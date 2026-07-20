-- #701 (epic #700): there is no user-level platform admin anymore.
--
-- The platform tier is a HOST credential (the static DOKTOK_TENANT_TOKENS used by console
-- scripts), never a user identity: the is_platform_admin flag, its grant endpoint, and the UI
-- grant are removed. Clean cut - there is no production instance, so existing flags are simply
-- dropped; console workflows are unaffected (they never used the flag).
--
-- Rollback: ALTER TABLE users ADD COLUMN is_platform_admin boolean NOT NULL DEFAULT false;

ALTER TABLE users DROP COLUMN IF EXISTS is_platform_admin;
