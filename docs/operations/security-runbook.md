# Limited-production security & privacy runbook

Operational security guide for exposing DokTok NG in the hybrid N95 deployment (M11 #341 /
DEVOPS-12). Read alongside [ADR-0020](../adr/ADR-0020-hybrid-deployment-topology.md),
[ADR-0006](../adr/ADR-0006-local-first-no-egress-security.md), and the
[deployment guide](deployment-trigkey-n95.md).

## Privacy posture: content egresses to OpenAI

This deployment deliberately departs from the local-first / no-egress default:

- With the **pipeline on OpenAI**, document text (the enrichment head, and more for some features)
  is sent to OpenAI for metadata, classification, NER, and record extraction.
- With **RAG on OpenAI**, the retrieved chunks and the user's question are sent to OpenAI for the
  answer and the rerank.
- **OCR and embeddings stay local** (the local OCR engine — RapidOCR/OpenVINO on the N95 — plus the
  Ollama embedder); pgvector and the file tree never leave the box.

`DOKTOK_NO_EGRESS=true` blocks OpenAI entirely (APP-3): the system refuses to egress and falls back
to the local model. The hybrid requires `DOKTOK_NO_EGRESS=false` as the explicit opt-in. The actual
outbound traffic is then constrained at the host firewall, not in the app. Communicate this to
stakeholders before ingesting sensitive material, and review OpenAI's data-handling terms. If
on-prem confidentiality is required, use the separate-LAN-Ollama-host option in ADR-0020 instead.

## Exposure checklist (before going live)

The full production variable list (REQUIRED vs optional) lives in the tracked
[`.env.production.example`](../../.env.production.example) template; bootstrap `.env.production` from it
once on the box per the [fresh-box runbook section 3](deploy-fresh-box-runbook.md).

- [ ] TLS enforced: `DOKTOK_SITE_ADDRESS` is a domain (auto-HTTPS) or an `https://` host with
      `tls internal`; nothing is served on plain HTTP to untrusted networks.
- [ ] Tenant tokens rotated off the `dev-token-*` defaults; long and random. `DOKTOK_API_TOKEN`
      (the Caddy edge token) is one of `DOKTOK_TENANT_TOKENS`.
- [ ] `DOKTOK_SECRETS_KEY` set so the OpenAI key is encrypted at rest (APP-8).
- [ ] If password login is enabled: a dedicated `DOKTOK_AUTH_JWT_SECRET` set, at least 32 bytes
      (the backend warns at startup otherwise - do not lean on the `DOKTOK_SECRETS_KEY` fallback in
      production, rotating sessions should not re-key stored secrets); `DOKTOK_TRUSTED_PROXY=true`
      only because Caddy fronts the API; admins minimized; unused invitations expired or their
      users removed (ADR-0024).
- [ ] Outbound firewall: default-deny, allow only 443 to `api.openai.com` + DNS
      (`deploy/firewall-openai-only.example.nft`).
- [ ] Only Caddy publishes host ports (80/443); db, ollama, gotenberg, backend are internal-only.
- [ ] Rate limiting on (`DOKTOK_RATE_LIMIT_PER_MINUTE` > 0) and `DOKTOK_LOG_FORMAT=json`.
- [ ] Backups run on a schedule, encrypted and off-box; restore tested against staging (DEVOPS-6).
- [ ] No secrets in images or logs (the JSON logger redacts keys/bearer tokens; images are built
      from a `.dockerignore` that excludes `.env*`).

## Identity and access management (EPIC #523)

Full design in [ADR-0024](../adr/ADR-0024-tenant-user-management-and-rbac.md). The operational
levers:

- **Least privilege via roles.** `viewer` < `editor` < `admin`: reads pass for any authenticated
  caller, content writes need editor, settings writes and all of `/api/v1/admin/*` need admin.
  Keep members at viewer unless they ingest/edit; keep admins to a minimum. A tenant-scoped static
  token (`DOKTOK_TENANT_TOKENS`, including the Caddy edge token) has **no user identity and acts as
  admin** - treat every static token as an admin credential.
- **Platform owners (ADR-0025).** Deployment-spanning surfaces - portable backup export/restore,
  the DRP drill, model-stack writes (`PUT /settings/ai`, `PUT /settings/ocr`), and tenant
  provisioning - are gated to platform admins: host-provisioned static tokens (which are therefore
  also **platform** credentials - guard them accordingly), and users flagged `is_platform_admin`
  (migration `0052`). Grant only via `POST /api/v1/admin/users/{id}/platform-admin` from an existing
  platform admin; there is no self-bootstrap and no self-revoke. DB-minted user-less api tokens are
  tenant admins but never platform admins. Tenant admins keep tenant-scoped user management
  (users, roles, passwords, invitations, tokens) and read-only DRP status. On a multi-tenant
  deployment, review who holds the flag (`GET /api/v1/admin/users` shows it per user) and keep it
  to the operators who would also hold the backup passphrases.
- **Enabling password login (opt-in).** Set `DOKTOK_AUTH_JWT_SECRET` (at least 32 bytes, e.g.
  `openssl rand -base64 48`; without it, `DOKTOK_SECRETS_KEY` is used as the fallback signing
  secret, and with neither, login is disabled with a 503). The backend logs a loud startup warning
  for a short or fallback secret. Session JWTs live `DOKTOK_AUTH_ACCESS_TTL_SECONDS` (default
  3600) - keep the TTL modest, since a session cannot be individually revoked before expiry. The
  SPA holds the JWT in memory + sessionStorage only (per-tab, gone on close).
- **Brute-force posture.** Login attempts are throttled before any credential work:
  `DOKTOK_LOGIN_RATE_PER_MINUTE` per (tenant, email) (default 5) and
  `DOKTOK_LOGIN_IP_RATE_PER_MINUTE` per source IP (default 20), answered with 429 + `Retry-After`.
  Throttling, never account lockout (lockout is a denial-of-service primitive). Set
  `DOKTOK_TRUSTED_PROXY=true` behind Caddy so the per-IP key uses `X-Forwarded-For`; leave it false
  when clients reach the API directly, or the header becomes spoofable. Concurrent scrypt
  verifications are capped (`DOKTOK_LOGIN_MAX_CONCURRENT_VERIFIES`, default 4) so login cannot
  exhaust the API workers.
- **Revoke-all sessions**: rotate `DOKTOK_AUTH_JWT_SECRET` and restart the backend. Every
  outstanding session JWT becomes invalid immediately.
- **Revoke one person immediately**: deactivate the user
  (`POST /api/v1/admin/users/{id}/deactivate`, or the Admin tab). Enforcement is in the request
  path, not at login: the user's session JWTs **and** API tokens stop working on their next
  request, regardless of TTL. Self-deactivation is blocked so an admin cannot lock themselves out.
- **Revoke one DB API token**: `DELETE /api/v1/admin/tokens/{id}` (or the Admin tab) - effective
  immediately, no restart. Only a token's sha256 is stored; the plaintext is shown exactly once at
  issue time.
- **Invitations** expire after `DOKTOK_AUTH_INVITE_TTL_HOURS` (default 168). The invite token is a
  one-time credential - deliver it over a private channel; an unaccepted invited user cannot
  authenticate.
- **Dev seed hygiene.** `make seed-dev` refuses to run outside a local/dev environment with a
  loopback database and never hardcodes passwords, so seeded demo accounts cannot reach
  production. Do not carry the `dev` tenant onto an exposed box.
- **Audit**: every login attempt (`auth.login.succeeded` / `auth.login.failed`, with normalized
  email and client IP) plus all administration and membership events (role changes, password
  resets, token issue/revoke, invites, deactivations) are recorded in the activity log with the
  acting user (or tenant, for the login-less operator) as the actor. Review failed-login bursts -
  the throttle slows an attacker but the trail is where you notice one.

## Incident response

**Suspected OpenAI key exposure**
1. Revoke the key in the OpenAI dashboard.
2. Set a new key via Settings -> AI (or update `DOKTOK_OPENAI_API_KEY` and re-seed), then restart
   the backend + worker.
3. Review OpenAI usage for anomalies.

**Suspected tenant-token exposure**
1. A **DB-issued API token**: revoke it via `DELETE /api/v1/admin/tokens/{id}` or the Admin tab -
   effective immediately, no restart. If it was user-bound, consider also deactivating the user.
2. A **static token**: replace it in `DOKTOK_TENANT_TOKENS` (and `DOKTOK_API_TOKEN` if it was the
   edge token); restart the backend and Caddy. Old static tokens stop working on restart. Remember
   a static token acts as admin.

**Suspected session-JWT or signing-secret exposure**
1. If one user's session leaked: deactivate that user (blocks the session on its next request),
   then reactivate and reset their password.
2. If the signing secret may have leaked: rotate `DOKTOK_AUTH_JWT_SECRET` and restart the backend -
   all outstanding sessions are invalidated. If the fallback `DOKTOK_SECRETS_KEY` was the signing
   secret, see its rotation note below (it also re-keys the stored OpenAI key).

**Rotating `DOKTOK_SECRETS_KEY`**
Changing it makes the stored (encrypted) OpenAI key undecryptable. After rotating, re-enter the
OpenAI key via Settings (or re-seed) so it is re-encrypted under the new master key.

## Observability

- **Health**: `GET /health` (liveness) and `GET /ready` (dependency-aware: DB + Ollama hard,
  Gotenberg + OpenAI soft, plus worker-heartbeat staleness). Point an external uptime check at
  `/health` through Caddy and alert on failure.
- **Metrics**: `GET /metrics` (token-gated, Prometheus text) exposes request counts/latency, uptime,
  and the worker heartbeat age. Scrape it for the 8 GB box's key signal - memory headroom - and the
  worker-liveness gauge.
- **Logs**: `DOKTOK_LOG_FORMAT=json` emits structured logs with `request_id` + `tenant_id` and
  secret redaction; container logs are size-capped + rotated (json-file driver) so they don't fill
  the SSD.
