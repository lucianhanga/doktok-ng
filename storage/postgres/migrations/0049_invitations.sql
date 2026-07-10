-- #557 (EPIC #523): tenant membership invitations. An admin invites an email to a tenant; a user
-- row is created immediately with status='invited' and this table holds the one-time acceptance
-- token (only its sha256) + expiry. The invitee accepts to set a password and flip to 'active'.
-- Additive + idempotent.
--
-- The plaintext token is NEVER stored - only token_sha256 (UNIQUE for O(1) lookup at accept time).
-- user_id FK-cascades so revoking the user removes its pending invitation.
--
-- Rollback: DROP TABLE invitations;

CREATE TABLE IF NOT EXISTS invitations (
    id            text PRIMARY KEY,
    tenant_id     text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id       text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email         text NOT NULL,
    role          text NOT NULL DEFAULT 'viewer',
    token_sha256  text NOT NULL UNIQUE,
    expires_at    timestamptz NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    accepted_at   timestamptz
);

CREATE INDEX IF NOT EXISTS idx_invitations_tenant ON invitations(tenant_id);
