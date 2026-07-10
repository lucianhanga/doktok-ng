# ADR-0024: Tenant and user management with login, RBAC, and per-user state

## Status

Accepted (EPIC #523; all layers #554-#560 shipped and merged).

Extends [ADR-0007](ADR-0007-multi-tenancy-shared-db-tenant-id.md) (multi-tenancy) and delivers the
"Later (DB-backed)" token store that [ADR-0008](ADR-0008-token-authentication.md) planned.

## Context

Until this epic, DokTok NG's entire identity model was the static `DOKTOK_TENANT_TOKENS` env map
(ADR-0008): one opaque bearer token per tenant, no users, no roles, no revocation short of editing
the env and restarting, and every audit row for user-initiated actions attributed only to the
tenant. That is exactly right for the local-first single-operator deployment, but it blocks:

- **multiple people in one tenant** (a household or small team sharing one document store),
- **least privilege** (a read-only viewer vs. someone who may ingest/delete vs. an administrator),
- **attribution** (which person deleted a document or changed a setting),
- **credential lifecycle** (invite someone, revoke a leaked token, deactivate a member immediately),
- **administration without shell access** (member/token management from the UI, not `.env` edits).

The constraint that shaped everything: the single-operator, zero-configuration, local-first posture
(ADR-0006) must keep working **unchanged** - no login screen, no DB rows, no new required env vars.

## Decision

Build the identity stack as seven thin layers behind the existing ADR-0008 resolution seam, each
opt-in, so a deployment adopts only what it configures.

### 1. DB-backed tenant/user/token registry (#554)

A `TenantRegistry` port (`contracts/doktok_contracts/ports.py`) with two implementations:
`InMemoryTenantRegistry` (`core/doktok_core/security/inmemory.py`, tests) and
`PostgresTenantRegistry` (`storage/postgres/doktok_storage_postgres/repositories.py`). Migrations
`0043` (tenants, users, api_tokens), `0046` (users.password_hash), `0047` (users.role), `0049`
(invitations) create the data model; all additive and idempotent. An API token's plaintext is never
stored - only its sha256 (`token_sha256`, UNIQUE for O(1) lookup) plus a short `token_prefix` for
display. Resolution order in `resolve_token`: DB registry first, then the static
`DOKTOK_TENANT_TOKENS` map as the local-first/dev fallback - existing deployments keep working with
zero DB rows.

### 2. Password login and stateless session JWTs (#555)

- **Passwords**: stdlib `hashlib.scrypt` (`core/doktok_core/security/passwords.py`) - a memory-hard
  KDF with no new dependency. Each hash is self-describing
  (`scrypt$<n>$<r>$<p>$<salt>$<dk>`), so cost parameters can be raised later without invalidating
  stored hashes. Verification is constant-time.
- **Sessions**: `POST /api/v1/auth/login` verifies email/password against the registry and mints a
  short-lived HS256 JWT (`core/doktok_core/security/sessions.py`, PyJWT) carrying tenant + user.
  No server-side session store (local-first: nothing extra to run). `GET /api/v1/auth/me` returns
  the caller's identity and requires a user-scoped credential.
- **One seam**: `resolve_credential` (`core/doktok_core/security/auth.py`) routes a JWT-shaped
  bearer (two dots) to the session verifier first; anything else falls through to the opaque-token
  path (DB registry, then static map). Every existing endpoint accepts a session JWT with no
  per-route change.
- **Opt-in**: the JWT signing secret is `DOKTOK_AUTH_JWT_SECRET`, falling back to
  `DOKTOK_SECRETS_KEY`; with neither set, `/auth/login` returns 503 and the token paths work
  unchanged. Access TTL is `DOKTOK_AUTH_ACCESS_TTL_SECONDS` (default 3600).

### 3. RBAC: viewer < editor < admin (#556)

`Role` enum + `role_at_least`/`parse_role` in `core/doktok_core/security/roles.py`; the user's role
lives in `users.role` (default `viewer`, fail-closed on anything unknown). Enforcement is
method-aware and applied at `include_router` in `apps/backend/doktok_api/main.py`, not per handler:

- `make_write_guard(role)`: GET/HEAD/OPTIONS pass for any authenticated caller; POST/PUT/PATCH/
  DELETE require the router's minimum role (403 otherwise). Content routers (ingestion, documents,
  entities, chat, search, ...) require **editor** for writes; the settings router requires
  **admin** for writes.
- `require_admin`: gates **every** method (reads included) - applied to the admin router, since
  member/token listings must not be readable by non-admins.

**The local-first pivot**: `resolve_caller_role` maps a tenant-scoped credential with *no user
identity* (a static `DOKTOK_TENANT_TOKENS` entry, or a user-less DB api_token) to **admin**. A
single-operator deployment therefore keeps full access - including the new Admin tab - with no
configuration at all. A user-scoped caller's role always comes from the registry (authoritative and
revocable), defaulting to viewer whenever it cannot be resolved.

### 4. Audit actor attribution (#560)

`actor_identity(TenantContext)` (`core/doktok_core/audit/logger.py`) returns the authenticated
user's id when the caller logged in (session JWT or user-bound token), else the tenant id. Every
user-initiated audit and KG-provenance call site (documents, entities, settings, admin routers)
uses it, so the trail names the person when there is one and degrades to the tenant for the
login-less operator. Worker/system actors keep their literal actor + kind and stay distinguishable.
New `AuditEventType` values cover administration (`tenant.created`, `user.created`,
`user.role_changed`, `user.password_reset`, `api_token.issued`, `api_token.revoked`) and membership
(`user.invited`, `user.deactivated`, `user.reactivated`, `user.invite_accepted`).

### 5. Admin API and Admin UI (#559)

`apps/backend/doktok_api/routers/admin.py`, prefix `/api/v1/admin`, admin-gated for every method:
tenants (list/create), users (list/create, set role, set password, deactivate/reactivate),
invitations (create), API tokens (list/issue/revoke). Security properties:

- Reads never return credential material: user listings exclude the password hash; token listings
  expose only the short `token_prefix`.
- An issued API token's plaintext is returned **exactly once**, at creation (256-bit
  `secrets.token_urlsafe`), and never stored.
- Everything is scoped to the caller's tenant; every mutation is audited with the acting admin as
  actor.

The UI gains an **Admin** tab (`apps/ui/src/AdminPanel.tsx`): members with role/status controls,
invitations, and token issue/revoke, with one-time secrets shown once with a copy button.

### 6. Invitations and immediate deactivation (#557)

- **Invite**: `POST /api/v1/admin/invitations` creates a user with status `invited` plus a one-time
  acceptance token (sha256 stored, expiry from `DOKTOK_AUTH_INVITE_TTL_HOURS`, default 168 = 7
  days). The invitee calls the **public** `POST /api/v1/auth/accept-invite` with the token and a
  chosen password; that sets the password, activates the user, and consumes the invitation. A
  single generic error avoids disclosing whether a token exists.
- **Deactivation is authoritative, not advisory**: `require_tenant` rejects any credential that
  resolves to a user whose status is not `active`. This blocks a deactivated (or not-yet-accepted
  `invited`) user's session JWTs **and** API tokens on the very next request, regardless of token
  TTL - the immediate revocation lever. An admin cannot deactivate themselves (lockout guard).

### 7. Per-user server-side preferences (#558)

A `UserPreferenceRepository` port with in-memory (`core/doktok_core/preferences/inmemory.py`) and
Postgres implementations; migration `0048` creates `user_preferences` keyed
`(tenant_id, subject, key)` with jsonb values. The **subject is `actor_identity`**: a logged-in
user gets their own bucket, the login-less operator gets one persistent per-tenant bucket. Router
`GET/PUT/DELETE /api/v1/preferences` carries no role guard - any authenticated caller manages only
their own preferences (PUT merges; DELETE removes one key).

The UI sync is **transparent** (`apps/ui/src/persist.ts`): `loadJSON`/`saveJSON` keep their exact
synchronous localStorage semantics (no component changed); writes mirror to the server in a batched
fire-and-forget PUT; `main.tsx` hydrates the localStorage cache from the server before rendering.
Offline or unauthorized, the app silently runs local-only.

### The token-free SPA decision

The SPA remains deliberately **token-free and login-screen-free**. In dev, the Vite proxy injects
`DOKTOK_DEV_TOKEN` as the bearer (`apps/ui/vite.config.ts`); in production, the Caddy edge does the
same. The Admin tab and preference sync therefore run under the proxy-injected admin identity. A
full in-browser login flow (login form, token storage in the browser, session refresh) was
**deliberately deferred** - it adds an attack surface (token storage in the browser) and UX that
the current deployments do not need, while the API side (this epic) already supports it fully when
a login UI is added later.

### Security posture summary

- Passwords: memory-hard scrypt, per-password salt, parameters stored per-hash, constant-time
  verify; a user without a password cannot password-login.
- Login: generic `invalid email or password` for every failure; a decoy hash is verified when the
  email is unknown so response timing does not reveal account existence.
- Tokens and invites: high-entropy (256-bit), only sha256 persisted, plaintext shown exactly once.
- Session JWTs: short TTL (1 h default); rotating the signing secret is the revoke-all lever;
  deactivation revokes per-user immediately (see layer 6).
- RBAC fails closed: unknown/unresolvable roles become viewer; admin endpoints reject non-admin
  reads and writes.

## Consequences

- **Local-first is preserved exactly**: no config, no DB rows, no login - the static token still
  gives the single operator full (admin) access, now including the Admin tab and a per-tenant
  preference bucket.
- Multi-user tenants get real accounts, least-privilege roles, invitations, immediate deactivation,
  and per-person audit attribution - all manageable from the UI.
- Two credential families now flow through one resolution seam; any future scheme (OIDC/SSO) slots
  in at `resolve_credential` without touching routes.
- Sessions are stateless: nothing new to run or persist, but an individual JWT cannot be revoked
  before expiry except by deactivating the user or rotating the secret (accepted; TTL is short).
- Role enforcement at `include_router` means a **new router must be registered with the right
  guard** - forgetting one leaves its writes gated only by authentication. (The admin router is
  double-gated: at the include and per-route.)
- The static env map remains a fallback; deployments that migrate fully to DB tokens should shrink
  `DOKTOK_TENANT_TOKENS` to the proxy/edge token only.
- Preferences are opaque jsonb owned by the UI: flexible, but the backend cannot validate or
  migrate their shapes.

Related: [ADR-0007](ADR-0007-multi-tenancy-shared-db-tenant-id.md) (tenant isolation this builds
on), [ADR-0008](ADR-0008-token-authentication.md) (the token seam and the static map, now the
fallback tier), [ADR-0006](ADR-0006-local-first-no-egress-security.md) (the local-first posture the
design preserves).
