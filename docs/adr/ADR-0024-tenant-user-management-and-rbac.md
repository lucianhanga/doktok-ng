# ADR-0024: Tenant and user management with login, RBAC, and per-user state

## Status

Accepted (EPIC #523: layers #554-#560, plus the login-experience phases from the CISO review -
hardening, dev seed, and the in-browser login screen - all shipped and merged).

Amended by [ADR-0025](ADR-0025-platform-owner-tier.md) (platform-owner tier: deployment-spanning
surfaces are gated to platform admins, not merely tenant admins).

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
(ADR-0006) must keep working **unchanged** - no mandatory login, no DB rows, no new required env
vars.

## Decision

Build the identity stack as thin layers behind the existing ADR-0008 resolution seam, each opt-in,
so a deployment adopts only what it configures.

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
  stored hashes. Verification is constant-time. `validate_password` enforces a minimum of 12
  characters (max 128) on **every** path that sets a password (admin set, invite accept, dev seed).
- **Sessions**: `POST /api/v1/auth/login` verifies email/password against the registry and mints a
  short-lived HS256 JWT (`core/doktok_core/security/sessions.py`, PyJWT) carrying tenant + user.
  No server-side session store (local-first: nothing extra to run). `GET /api/v1/auth/me` returns
  the caller's identity and requires a user-scoped credential. `GET /api/v1/auth/config` is public
  and reports only whether login is enabled, so a client can choose its mode up front.
- **One seam**: `resolve_credential` (`core/doktok_core/security/auth.py`) routes a JWT-shaped
  bearer (two dots) to the session verifier first; anything else falls through to the opaque-token
  path (DB registry, then static map). Every existing endpoint accepts a session JWT with no
  per-route change.
- **Opt-in**: the JWT signing secret is `DOKTOK_AUTH_JWT_SECRET`, falling back to
  `DOKTOK_SECRETS_KEY`; with neither set, `/auth/login` returns 503 and the token paths work
  unchanged. Access TTL is `DOKTOK_AUTH_ACCESS_TTL_SECONDS` (default 3600).

### 3. Login-endpoint hardening (CISO phase 1)

The login endpoint is the only pre-auth credential surface, so it gets dedicated defenses
(`apps/backend/doktok_api/routers/auth.py`, wired in `main.py`, configured in
`core/doktok_core/config.py`):

- **Brute-force throttling, before any credential work**: two token buckets return
  429 + `Retry-After` - per (tenant, email) (`DOKTOK_LOGIN_RATE_PER_MINUTE`, default 5) and per
  source IP (`DOKTOK_LOGIN_IP_RATE_PER_MINUTE`, default 20). Throttle, never hard-lock: an account
  lockout is an account-DoS primitive. Setting a bucket to 0 disables it.
- **Spoof-resistant IP key**: `X-Forwarded-For` is honored only when `DOKTOK_TRUSTED_PROXY=true`
  (default false) - otherwise a client could spoof the header to dodge the per-IP limit.
- **Resource cap**: a semaphore bounds concurrent scrypt verifications
  (`DOKTOK_LOGIN_MAX_CONCURRENT_VERIFIES`, default 4) so login cannot exhaust the sync worker pool
  or memory regardless of the rate limits.
- **No user enumeration**: a single generic 401 (`invalid email or password`) for every failure,
  and a decoy hash is verified when the email is unknown so the "no such user" path costs the same
  as "wrong password" (no timing oracle).
- **Weak-secret startup warning**: with login enabled, the backend warns loudly at startup when
  `DOKTOK_AUTH_JWT_SECRET` is shorter than 32 bytes (short HS256 secrets are offline-crackable
  from any captured token) or when it is absent and sessions are being signed with the
  `DOKTOK_SECRETS_KEY` fallback (reusing the envelope-encryption key for signing widens the blast
  radius of a leak). Warn rather than refuse, so a local-first dev box is never wedged.
- **Login audit**: every attempt is recorded (`AUTH_LOGIN_SUCCEEDED` / `AUTH_LOGIN_FAILED`) with
  the normalized email and client IP - never the password.

### 4. RBAC: viewer < editor < admin (#556)

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

### 5. Audit actor attribution (#560)

`actor_identity(TenantContext)` (`core/doktok_core/audit/logger.py`) returns the authenticated
user's id when the caller logged in (session JWT or user-bound token), else the tenant id. Every
user-initiated audit and KG-provenance call site (documents, entities, settings, admin routers)
uses it, so the trail names the person when there is one and degrades to the tenant for the
login-less operator. Worker/system actors keep their literal actor + kind and stay distinguishable.
New `AuditEventType` values cover administration (`tenant.created`, `user.created`,
`user.role_changed`, `user.password_reset`, `api_token.issued`, `api_token.revoked`), membership
(`user.invited`, `user.deactivated`, `user.reactivated`, `user.invite_accepted`), and login
attempts (layer 3).

### 6. Admin API and Admin UI (#559)

`apps/backend/doktok_api/routers/admin.py`, prefix `/api/v1/admin`, admin-gated for every method:
`GET /context` (tenant + caller summary for the UI), tenants (list/create; ids are
server-generated opaque GUIDs), users (list/create, set role, set password, deactivate/reactivate),
invitations (create), API tokens (list/issue/revoke). Security properties:

- Reads never return credential material: user listings exclude the password hash; token listings
  expose only the short `token_prefix`.
- An issued API token's plaintext is returned **exactly once**, at creation (256-bit
  `secrets.token_urlsafe`), and never stored.
- Everything is scoped to the caller's tenant; every mutation is audited with the acting admin as
  actor.

The UI gains an **Admin** tab (`apps/ui/src/AdminPanel.tsx`), organized as a single-tenant console:
a tenant-context header, members with role/status controls, invitations, token issue/revoke (one-
time secrets shown once with a copy button), and an instance-administration card.

### 7. Invitations and immediate deactivation (#557)

- **Invite**: `POST /api/v1/admin/invitations` creates a user with status `invited` plus a one-time
  acceptance token (sha256 stored, expiry from `DOKTOK_AUTH_INVITE_TTL_HOURS`, default 168 = 7
  days). The invitee calls the **public** `POST /api/v1/auth/accept-invite` with the token and a
  chosen password; that sets the password, activates the user, and consumes the invitation. A
  single generic error avoids disclosing whether a token exists.
- **Deactivation is authoritative, not advisory**: `require_tenant` rejects any credential that
  resolves to a user whose status is not `active`. This blocks a deactivated (or not-yet-accepted
  `invited`) user's session JWTs **and** API tokens on the very next request, regardless of token
  TTL - the immediate revocation lever. An admin cannot deactivate themselves (lockout guard).

### 8. Per-user server-side preferences (#558)

A `UserPreferenceRepository` port with in-memory (`core/doktok_core/preferences/inmemory.py`) and
Postgres implementations; migration `0048` creates `user_preferences` keyed
`(tenant_id, subject, key)` with jsonb values. The **subject is `actor_identity`**: a logged-in
user gets their own bucket, the login-less operator gets one persistent per-tenant bucket. Router
`GET/PUT/DELETE /api/v1/preferences` carries no role guard - any authenticated caller manages only
their own preferences (PUT merges; DELETE removes one key).

The UI sync is **transparent** (`apps/ui/src/persist.ts`): `loadJSON`/`saveJSON` keep their exact
synchronous localStorage semantics (no component changed); writes mirror to the server in a batched
fire-and-forget PUT; the app hydrates the localStorage cache from the server before rendering.
Offline or unauthorized, the app silently runs local-only.

### 9. The token-free SPA, with an opt-in login screen (CISO phase 3)

The SPA bundle remains **token-free**: no credential is ever baked in. What changed is that the
app now supports two modes, chosen at boot by `AuthGate` (`apps/ui/src/AuthGate.tsx`) from the
public `GET /api/v1/auth/config`:

- **Token-free mode (default)**: login disabled (or config unreachable) - the dev proxy or the
  production edge injects the bearer, exactly as before. Zero-configuration local-first keeps
  working with no login screen.
- **Login mode**: login enabled and no active session - a login screen
  (`apps/ui/src/LoginScreen.tsx`: tenant + email + password) exchanges credentials for a session
  JWT; a signed-in bar shows the identity/role with a log-out button.

Session-storage decision (`apps/ui/src/session.ts`): the JWT is kept **in memory, mirrored to
sessionStorage** - it survives a tab reload, dies with the tab, and is not shared across tabs. Not
localStorage (persists across restarts) and not cookies (would force CSRF handling onto a
deliberately header-bearer, `allow_credentials=false` API). Exposure is bounded by the short TTL
plus the per-request status check (deactivation kills a stolen token immediately). A fetch wrapper
(`installAuthFetch`) attaches the bearer to same-origin API calls and routes any 401 back to the
login screen.

**The dev-proxy interplay**: the Vite proxy (`apps/ui/vite.config.ts`) injects `DOKTOK_DEV_TOKEN`
**only when the request carries no Authorization header**. Once a developer logs in, the SPA's own
JWT wins - otherwise every dev login would silently resolve to the static tenant and sessions/RBAC
could never be exercised in dev. Anonymous (token-free) requests still get the dev token.

### 10. Gated dev seed for role-based logins (CISO phase 2)

`make seed-dev` (`scripts/seed-dev.sh` -> `scripts/_seed_dev.py` -> `core/doktok_core/dev/seed.py`)
creates a `dev` tenant with one active user per role - `dev-admin@doktok.local`,
`dev-editor@doktok.local`, `dev-viewer@doktok.local` - so RBAC is actually exercisable from the
login screen. Three independent gates keep seeded demo credentials out of production (the classic
default-credentials path, CWE-1392):

1. never wired into startup or migrations - it runs only when explicitly invoked;
2. refuses unless the environment is `local`/`dev` **and** the database is loopback (override with
   `--allow-remote`; the env gate is absolute);
3. no hardcoded passwords - each password comes from `DOKTOK_DEV_SEED_PASSWORD` (min 12 chars,
   reproducible logins) or is generated per user and printed exactly once.

Idempotent: re-running leaves existing users untouched; `make seed-dev ARGS=--reset` rotates
passwords and re-syncs roles. The tenant id is `dev`, deliberately not `test%` (which the
integration-test cleanup wipes). The seed is a dev convenience only - no automated test depends on
it.

### Security posture summary

- Passwords: memory-hard scrypt, per-password salt, parameters stored per-hash, constant-time
  verify, minimum length 12 on every set-password path; a user without a password cannot
  password-login.
- Login: generic 401, decoy-hash timing defense, per-account and per-IP throttling (429 +
  Retry-After), concurrency-capped verification, spoof-resistant IP keying, audited attempts, and
  a loud startup warning for a weak or fallback signing secret.
- Tokens and invites: high-entropy (256-bit), only sha256 persisted, plaintext shown exactly once.
- Session JWTs: short TTL (1 h default), held in memory + sessionStorage (never localStorage or
  cookies); rotating the signing secret is the revoke-all lever; deactivation revokes per-user
  immediately (see layer 7).
- RBAC fails closed: unknown/unresolvable roles become viewer; admin endpoints reject non-admin
  reads and writes.
- Dev seed: triple-gated (explicit invocation, local/dev + loopback DB, no hardcoded passwords).

## Consequences

- **Local-first is preserved exactly**: no config, no DB rows, no login - the static token still
  gives the single operator full (admin) access, now including the Admin tab and a per-tenant
  preference bucket. Login, and with it the login screen, appears only when a signing secret is
  configured.
- Multi-user tenants get real accounts, least-privilege roles, invitations, immediate deactivation,
  an in-browser login, and per-person audit attribution - all manageable from the UI.
- Two credential families now flow through one resolution seam; any future scheme (OIDC/SSO) slots
  in at `resolve_credential` without touching routes.
- Sessions are stateless: nothing new to run or persist, but an individual JWT cannot be revoked
  before expiry except by deactivating the user or rotating the secret (accepted; TTL is short).
- sessionStorage scoping means a login is per-tab and does not survive closing the tab - a
  deliberate exposure/convenience trade-off.
- Role enforcement at `include_router` means a **new router must be registered with the right
  guard** - forgetting one leaves its writes gated only by authentication. (The admin router is
  double-gated: at the include and per-route.)
- The static env map remains a fallback; deployments that migrate fully to DB tokens should shrink
  `DOKTOK_TENANT_TOKENS` to the proxy/edge token only. Every static token acts as an admin
  credential - treat it accordingly.
- Preferences are opaque jsonb owned by the UI: flexible, but the backend cannot validate or
  migrate their shapes.

Related: [ADR-0007](ADR-0007-multi-tenancy-shared-db-tenant-id.md) (tenant isolation this builds
on), [ADR-0008](ADR-0008-token-authentication.md) (the token seam and the static map, now the
fallback tier), [ADR-0006](ADR-0006-local-first-no-egress-security.md) (the local-first posture the
design preserves).
