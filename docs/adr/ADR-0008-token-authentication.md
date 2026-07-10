# ADR-0008: Token Authentication and Tenant Resolution

## Status

Accepted. Extended by [ADR-0024](ADR-0024-tenant-user-management-and-rbac.md) (EPIC #523), which
delivered the "Later (DB-backed)" token store below plus user login, RBAC, and tenant/member
administration; the static map described here remains the local-first fallback tier.

## Context

The backend API must be accessed securely. DokTok NG follows the PersonalAI pattern: a bearer token
checked with a constant-time comparison, fail-closed when unconfigured, with the server bound to
loopback by default. DokTok NG additionally needs the token to identify a **tenant** (ADR-0007), since
one deployment serves many tenants.

## Decision

DokTok NG authenticates API requests with a **bearer token that maps to a tenant**.

- Clients send `Authorization: Bearer <token>`.
- The backend resolves the presented token to a `tenant_id` using a configured map, comparing with a
  constant-time function (`secrets.compare_digest`) to avoid timing oracles.
- A `_require_tenant` FastAPI dependency protects all `/api/*` routes and yields the resolved
  `TenantContext`; `/health` is public.
- **Fail-closed**: if no tokens are configured, protected routes return an error rather than allowing
  access. The server binds to `127.0.0.1` by default and refuses a non-loopback bind when no tokens
  are configured.
- CORS is restricted to the configured (loopback) origins; the bearer token remains the real control.

### Token store

- **Now (static):** `DOKTOK_TENANT_TOKENS` holds a JSON map of `{ "<token>": "<tenant_id>" }`, loaded
  from the environment / `.env`. Tokens are plaintext in local config (gitignored). This mirrors
  PersonalAI's single static token, extended to many tenants.
- **Later (DB-backed):** `tenants` and `api_tokens` tables with hashed tokens, rotation, and
  revocation, behind the same resolution interface. Tracked as a follow-up.

## Consequences

Positive:

- simple, dependency-free local auth that still isolates tenants
- safe defaults (loopback, fail-closed, constant-time compare)
- a clear upgrade path to DB-backed, hashed, revocable tokens

Negative:

- static tokens cannot be rotated/revoked without a restart (acceptable until the DB-backed store)
- plaintext tokens in local config rely on file permissions and `.gitignore`

## Required controls

- constant-time token comparison; never log tokens
- fail-closed when unconfigured; loopback-by-default bind
- tenant identity comes only from the authenticated token, never from request input
- all authenticated access is auditable (audit events arrive with the audit milestone)
