# ADR-0025: Platform-owner tier for deployment-spanning surfaces

## Status

Accepted (2026-07-18; security audit finding F-01, ticket #613).
**Revised (2026-07-20, epic #700 / #701-#703): the tier is a HOST credential only.** The
`users.is_platform_admin` flag, the grant endpoint, and all UI platform surfaces are removed;
platform endpoints accept ONLY the static `DOKTOK_TENANT_TOKENS` (`via == "static"`). The
"Administrator tiers (2026-07-20 clarification)" section below is kept for history and superseded
by the "Administrator model (2026-07-20 revision)" section at the end.

Amends [ADR-0024](ADR-0024-tenant-user-management-and-rbac.md) by adding a deployment-level identity
attribute on top of the tenant-scoped RBAC.

## Context

The 2026-07-17 white-box API security audit found that several **deployment-spanning** surfaces are
reachable by ANY tenant admin (findings F-01…F-03, F-08, F-09): the portable backup export (a full
`pg_dump` of every tenant plus the whole files tree), the destructive whole-deployment restore, the
no-egress toggle and remote model URLs, deployment-global AI/OCR settings, and tenant
enumeration/provisioning. ADR-0024's identity model has exactly two tiers — tenant-scoped credentials
(static token / api token / session JWT) and per-tenant roles (viewer/editor/admin) — with no way to
express "deployment owner" as distinct from "tenant admin". The root cause is deliberate (local-first
single operator: a user-less tenant token resolves to `admin`), but the admin stack exists precisely
to support multiple tenants, so the model contradicts the feature set whenever tenant admins are
distinct principals.

## Decision

Introduce a **platform-owner tier** as a first-class identity attribute, orthogonal to the
tenant-scoped RBAC roles:

1. **`users.is_platform_admin`** (migration `0052`) — a DB flag on the user, NOT a role. Roles stay
   tenant-scoped; platform admin is deployment-level.
2. **Resolution at authentication** (`require_tenant`): static env tokens (`DOKTOK_TENANT_TOKENS`)
   are platform admins (host-provisioned — the host IS the platform, and this is the fresh-box
   bootstrap path); DB-minted user-less api tokens are tenant admins but NEVER platform admins (any
   tenant admin can mint those); user-bound credentials (session JWT / user api token) inherit the
   user's flag. `TokenResolution` carries a `via` marker (`db`|`static`|`jwt`) so the API does not
   re-derive the tier; the flag rides the same user fetch as the deactivation check (no extra query).
3. **Guard**: `require_platform_admin` = flag set AND admin role (fail closed), applied per-endpoint
   to deployment-spanning surfaces. This change gates the portable backup export (build / status /
   download); restore, the egress toggle, global settings, and tenant provisioning follow in their
   own tickets, reusing the same guard.
4. **Grant/revoke**: `POST /admin/users/{id}/platform-admin` — platform admins only, never through
   user creation (no self-bootstrap), self-revoke blocked (mirrors the self-deactivation guard), and
   audited (`user.platform_admin_changed`).
5. **Bootstrap/tooling**: static tokens (present in every deployment), `create-tenant
   --platform-admin`, and `seed_dev` marks the dev admin user so the UI persona exists in dev.
6. `/auth/me` and the admin user views expose the flag so the SPA can reflect platform status.

## Alternatives considered

- **`DOKTOK_PLATFORM_TENANTS` env allowlist** — rejected: config-as-identity, cannot express a
  user-level grant, and any default either breaks single-operator or silently permits everyone.
- **Extend the `Role` enum (`viewer < editor < admin < platform`)** — rejected: roles are
  tenant-scoped by design; platform ownership is deployment-level and would leak that into the
  hierarchy.
- **Docs-only (declare single-operator)** — rejected by the owner: the multi-tenant admin stack
  (ADR-0024) is a real feature; the fix must match it.
- **Remove portable backup from the API (host-only)** — rejected: deletes a shipped M12 feature the
  DRP panel uses.

## Consequences

- Backup export now returns 403 for tenant admins who are not platform admins — including DB-minted
  user-less tokens. Single-operator deployments on static tokens are unaffected; deployments that
  gave other tenant admins export rights must grant the flag once (via a platform admin or
  `create-tenant --platform-admin`).
- The guard is one dependency reused by the F-02/F-03/F-08/F-09 tickets — gating restore, egress,
  settings, and tenant provisioning is now a small change per endpoint.
- F-33 (user-less tokens always resolve to tenant admin) is unchanged and remains its own hardening
  ticket.

## Related files

- `apps/backend/doktok_api/dependencies.py` (`require_platform_admin`, platform resolution),
  `routers/settings.py` (export gating), `routers/admin.py` (grant endpoint), `routers/auth.py`
  (flag in `/auth/me`)
- `core/doktok_core/security/auth.py` (`via` tiers), `core/doktok_core/dev/seed.py`,
  `scripts/_create_tenant.py`
- `contracts/doktok_contracts/schemas.py` (`User.is_platform_admin`, `TenantContext.platform_admin`,
  `TokenResolution.via`), `contracts/doktok_contracts/ports.py` (`TenantRegistry.set_platform_admin`)
- `storage/postgres/migrations/0052_user_platform_admin.sql`
- Tests: `apps/backend/tests/test_platform_guard.py`, `test_platform_admin_api.py`,
  `core/tests/test_platform_admin.py`, `core/tests/test_dev_seed.py`,
  `storage/postgres/tests/test_tenant_registry_integration.py`

## Administrator tiers (2026-07-20 clarification, #696)

The deployment has THREE administrator personas; only two exist inside the app:

1. **System administrator — host console only, never an app account.** Runs the manual
   backup/recovery scripts (`deploy/backup.sh`, `deploy/restore.sh`), provisions tenants and
   their first admin users (`scripts/create-tenant.sh`), and owns the root systemd units that
   apply restores and drills. This persona authenticates to the host (ssh/console), not to the
   API; nothing in the UI represents it.
2. **Platform administrator** — this ADR's tier (static host tokens or `is_platform_admin`
   users): deployment-spanning surfaces (portable backup export/restore, DRP actions, model-stack
   and OCR defaults, no-egress posture, tenant provisioning).
3. **Tenant administrator** — per-tenant `admin` role: user management for their own tenant
   (members, roles, passwords, API tokens) and read-only DRP monitoring (status + history). A
   tenant admin can never create tenants, run recovery tasks, or change deployment defaults; the
   UI hides those surfaces and the API 403s them.

## Administrator model (2026-07-20 revision, epic #700)

The model is now TWO personas; the "platform administrator" user identity is gone:

1. **System administrator — host console only.** Does everything deployment-spanning: manual
   backups and recoveries (`deploy/backup.sh`, `deploy/restore.sh`), tenant + first-admin
   provisioning (`scripts/create-tenant.sh`), restore drills, console-global model-stack/OCR
   defaults and the no-egress lock, and portable backup export/restore. Mechanisms: the deploy
   scripts, file edits (env), or `curl` against the API with the static host token
   (`DOKTOK_TENANT_TOKENS` - the console credential, `via == "static"`). Nothing in the UI
   represents this persona.
2. **Tenant administrator** — per-tenant `admin` role, the only administrator who logs in
   through the UI. User management for their own tenant (members, roles, passwords, API tokens),
   read-only DRP monitoring, and - since epic #708 - their tenant's model-stack override and
   data-egress posture (Settings → Model stack; embedding/OCR stay deployment-global). The UI
   contains NO platform surfaces at all (no Instance Administration, no DRP actions, no
   console-global model-stack writes), and every platform endpoint 403s any session JWT or user
   api token.

Consequences: `users.is_platform_admin` was dropped (migration `0055`), along with the grant
endpoint, `TenantRegistry.set_platform_admin`, the `· platform` UI badge, and the seed's
`dev-admin` platform persona (it is a plain tenant admin; the static dev token is the console
credential). The restore-apply actor binding (F-34) now binds to the host credential's tenant
identity.
