# How automated testing works

How the DokTok NG test suite is organized, how to run it, and what CI checks before a merge. The
quality gate itself is defined in [CONTRIBUTING.md](../../CONTRIBUTING.md); this page is the
operator's map of the machinery. Auth/identity coverage (EPIC #523,
[ADR-0024](../adr/ADR-0024-tenant-user-management-and-rbac.md)) is called out explicitly because it
is security-relevant.

## The test layers

**1. Python unit and API tests (pytest, no database needed).** Each workspace package has its own
`tests/` directory; the roots are wired in `[tool.pytest.ini_options].testpaths` in
[`pyproject.toml`](../../pyproject.toml) (`apps/backend/tests`, `apps/worker/tests`, `core/tests`,
`contracts/tests`, `providers/*/tests`, `storage/postgres/tests`). API tests build the FastAPI app
per test with **in-memory implementations of the ports** - for example `InMemoryTenantRegistry` and
`InMemoryAuditLogRepository` constructed inside fixtures - so every test starts from an empty,
isolated state and **no persistent test credential ever ships**: users, passwords, and tokens exist
only inside the test process. Test fixtures that must look like secrets carry the
`# pragma: allowlist secret` marker (see the secret scan below).

**2. Postgres integration tests (pytest, real database required).** `storage/postgres/tests/`
exercises the real repositories - including `test_tenant_registry_integration.py` and
`test_user_preferences_integration.py` for the identity stack - against a live Postgres. They are
**skipped automatically when no database is reachable**, and they only ever touch tenants whose id
starts with `test` (`conftest.py` deletes `test%` rows before each run), so running them against
your local dev database never destroys your own data. Point `DOKTOK_TEST_DATABASE_URL` at a
separate database for full isolation; otherwise they use `DOKTOK_DATABASE_URL`.

**3. Frontend tests (Vitest + Testing Library, jsdom).** Colocated `*.test.ts(x)` files under
`apps/ui/src/`. There is no test server: tests **stub `fetch` per test** and assert on rendered
states. The shared setup (`apps/ui/src/test/setup.ts`) polyfills jsdom gaps (IntersectionObserver,
matchMedia); the session-bearer fetch wrapper is deliberately never installed in unit tests.

There is no end-to-end/browser-automation suite; manual QA of full flows is described per feature
in [running.md](running.md).

## Running the tests

```bash
make check          # everything CI runs locally: lint, typecheck, pytest, import-linter, JS suite
make test           # Python tests only (= uv run pytest; includes DB integration if reachable)
make js             # frontend typecheck + lint + Vitest
make js-test        # frontend Vitest only (= pnpm -r test)

# Narrower runs:
uv run pytest apps/backend/tests/test_auth_api.py            # one file
uv run pytest -k "throttle" apps/backend/tests               # by keyword
pnpm --filter @doktok/ui test LoginScreen                    # one UI test file
pnpm --filter @doktok/ui exec vitest                         # UI tests in watch mode
```

`make db` first if you want the Postgres integration layer to run instead of skip.

## What covers the auth/identity stack

| Area | Tests |
|---|---|
| Password hashing + policy (scrypt, min 12) | `core/tests/test_passwords.py` |
| Session JWTs (issue/verify/expiry) | `core/tests/test_sessions.py` |
| Credential resolution seam (JWT vs opaque token) | `core/tests/test_auth.py` |
| Role model (viewer < editor < admin, fail-closed) | `core/tests/test_roles.py` |
| Login / `/auth/me` / `/auth/config` API | `apps/backend/tests/test_auth_api.py` |
| Login hardening (throttle, 429 + Retry-After, decoy timing, semaphore, weak-secret warning, audit) | `apps/backend/tests/test_auth_hardening.py` |
| Admin API (tenants, users, roles, tokens; one-time secrets) | `apps/backend/tests/test_admin_api.py` |
| Invitations, accept-invite, deactivation/reactivation | `apps/backend/tests/test_membership_api.py` |
| RBAC enforcement on the routers (403 matrix) | `apps/backend/tests/test_rbac_api.py` |
| Audit actor attribution | `apps/backend/tests/test_audit_actor_api.py` |
| Per-user preferences API | `apps/backend/tests/test_preferences_api.py` |
| Dev seed logic + gating (`seed_guard`) | `core/tests/test_dev_seed.py` |
| Registry + preferences against real Postgres | `storage/postgres/tests/test_tenant_registry_integration.py`, `test_user_preferences_integration.py` |
| UI session storage + fetch wrapper (401 -> re-login) | `apps/ui/src/session.test.ts` |
| UI login form | `apps/ui/src/LoginScreen.test.tsx` |
| UI auth gate (token-free vs login mode) | `apps/ui/src/AuthGate.test.tsx` |
| UI preference sync | `apps/ui/src/persist.test.tsx` |

**`make seed-dev` is a dev convenience, not a test fixture.** No automated test depends on the
seeded `dev` tenant or its users; tests build their own registries in-process (layer 1) or use
`test%` tenants (layer 2). The seed exists only so a human can log in from the UI
(see [running.md](running.md#tenant-and-user-management-in-your-dev-environment)).

## What CI runs

One workflow, [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml), three jobs on every
push and pull request:

- **Python checks**: `ruff check`, `ruff format --check`, `mypy`, `uv run pytest`, and
  `lint-imports` (hexagonal boundaries). The job starts a `pgvector/pgvector:pg17` service
  container, so the Postgres integration tests **run for real in CI** - they are not skipped there.
- **Frontend checks**: `pnpm` typecheck, lint, Vitest, and a dependency audit.
- **Security scans**:
  - `detect-secrets` over every tracked file against
    [`.secrets.baseline`](../../.secrets.baseline). A new token-, password-, or key-looking literal
    **fails the build** - including in documentation and test files. Use obvious placeholders like
    `<dev-token>` in docs, or append `# pragma: allowlist secret` to a line that must contain a
    secret-shaped literal (the convention already used by the auth test fixtures). Reproduce the
    scan locally with:
    `git ls-files -z | xargs -0 uvx --from detect-secrets detect-secrets-hook --baseline .secrets.baseline`
  - `pip-audit` (informational for now: advisories are reported but do not fail the build).

The same secret scan is also wired as a pre-commit hook
([`.pre-commit-config.yaml`](../../.pre-commit-config.yaml)).

## Adding tests

Follow the layer that matches what you changed: pure domain logic gets a unit test in the owning
package's `tests/`; API behavior gets a `TestClient` test with in-memory ports in
`apps/backend/tests/`; SQL/repository behavior gets an integration test in
`storage/postgres/tests/` using a `test%` tenant id; UI behavior gets a colocated Vitest file with
a stubbed `fetch`. Before opening a PR, `make check` must pass (see
[CONTRIBUTING.md](../../CONTRIBUTING.md)).
