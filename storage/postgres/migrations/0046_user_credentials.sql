-- #555 (EPIC #523): user login credentials. Adds a password hash to the #554 users registry so a
-- user can authenticate and receive a session JWT (see core/security/passwords.py + sessions.py).
-- Additive + idempotent.
--
-- The column is NULLABLE: a user without a password (token-only, or a future SSO/OIDC identity)
-- simply cannot password-login. The plaintext password is NEVER stored - only a self-describing
-- scrypt digest ("scrypt$n$r$p$salt$dk").
--
-- Rollback: ALTER TABLE users DROP COLUMN password_hash;

ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash text;
