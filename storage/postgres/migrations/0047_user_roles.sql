-- #556 (EPIC #523): RBAC role on the #554 users registry. viewer | editor | admin, gating write
-- access in the API. Additive + idempotent.
--
-- Default 'viewer' (least privilege): a new DB user reads only until explicitly granted editor/admin
-- (set_user_role; tenant administration is #559). Local-first is unaffected - a tenant-scoped token
-- with no user resolves to admin in the app layer, so single-operator deployments keep full access.
--
-- Rollback: ALTER TABLE users DROP COLUMN role;

ALTER TABLE users ADD COLUMN IF NOT EXISTS role text NOT NULL DEFAULT 'viewer';
