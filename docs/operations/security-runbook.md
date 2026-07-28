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
to the local model. The same posture gates the Settings **test/probe endpoints** (#622): under
no-egress, `test-openai` is refused and `test-ollama`/`warmup-ollama` accept only loopback targets
- they cannot be used to reach the host's network. The hybrid requires `DOKTOK_NO_EGRESS=false` as
the explicit opt-in. The actual outbound traffic is then constrained at the host firewall, not in
the app. Communicate this to stakeholders before ingesting sensitive material, and review OpenAI's
data-handling terms. If on-prem confidentiality is required, use the separate-LAN-Ollama-host
option in ADR-0020 instead.

## Exposure checklist (before going live)

The full production variable list (REQUIRED vs optional) lives in the tracked
[`.env.production.example`](../../.env.production.example) template; bootstrap `.env.production` from it
once on the box per the [fresh-box runbook section 3](deploy-fresh-box-runbook.md).

- [ ] TLS enforced: `DOKTOK_SITE_ADDRESS` is a domain (auto-HTTPS) or an `https://` host with
      `tls internal`; nothing is served on plain HTTP to untrusted networks.
- [ ] Tenant tokens rotated off the `dev-token-*` defaults; long and random. `DOKTOK_API_TOKEN`
      (the Caddy edge token) is one of `DOKTOK_TENANT_TOKENS`.
- [ ] Edge trust boundary understood (#616): the Caddy edge injects `DOKTOK_API_TOKEN` only when a
      request has no `Authorization` of its own (logged-in JWTs pass through), but that token is a
      **platform credential** (ADR-0025) - anonymous callers to the port act as the platform owner.
      Either restrict the port to a trusted network, or enable password login so real users present
      their own credentials.
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
  caller, content writes need editor, settings reads and all of `/api/v1/admin/*` need admin.
  Keep members at viewer unless they ingest/edit; keep admins to a minimum. A tenant-scoped static
  token (`DOKTOK_TENANT_TOKENS`, including the Caddy edge token) has **no user identity and acts as
  admin** - treat every static token as an admin credential.
- **The console admin (ADR-0025, epic #700).** Deployment-spanning surfaces - portable backup
  export/restore, the DRP drill, console-global model-stack writes (`PUT /settings/ai`,
  `PUT /settings/ocr`), and tenant provisioning - accept ONLY the static host token
  (`via == "static"`); session JWTs and user api tokens always get 403, and the SPA carries no
  platform surfaces (no Instance Administration, no DRP actions). The static tokens are therefore
  the deployment's console credentials - guard them like ssh keys. Day-to-day deployment operations
  run on the host: `scripts/create-tenant.sh` (tenants + first admins), `deploy/backup.sh` /
  `deploy/restore.sh`, or `curl` with the static token for export/drill/global model-stack writes.
  There is no user platform flag to grant or review; tenant admins keep tenant-scoped user
  management, read-only DRP status, and - since epic #708 - their tenant's model-stack override
  (`PUT`/`DELETE /settings/ai/override`) and data-egress posture in the UI (Settings → Model stack).
  The console still owns the default layers and the `DOKTOK_NO_EGRESS_LOCK` floor; embedding and
  OCR stay deployment-global.
- **Enabling password login (opt-in).** Set `DOKTOK_AUTH_JWT_SECRET` (at least 32 bytes, e.g.
  `openssl rand -base64 48`; without it, `DOKTOK_SECRETS_KEY` is used as the fallback signing
  secret, and with neither, login is disabled with a 503). The backend logs a loud startup warning
  for a short or fallback secret. Session JWTs live `DOKTOK_AUTH_ACCESS_TTL_SECONDS` (default
  3600) - keep the TTL modest, since a session cannot be individually revoked before expiry. The
  SPA holds the JWT in memory + sessionStorage only (per-tab, gone on close).
- **Brute-force posture.** Login attempts are throttled before any credential work:
  `DOKTOK_LOGIN_RATE_PER_MINUTE` per (tenant, email) (default 5) and
  `DOKTOK_LOGIN_IP_RATE_PER_MINUTE` per source IP (default 20), answered with 429 + `Retry-After`.
  Throttling, never account lockout (lockout is a denial-of-service primitive). The shipped prod
  compose sets `DOKTOK_TRUSTED_PROXY=true` (Caddy fronts the API, #621) so the per-IP key uses the
  rightmost, proxy-appended `X-Forwarded-For` element - behind an appending proxy the left side is
  client-controlled and ignored. Keep it `false` when clients reach the API directly, or the header
  becomes spoofable. Concurrent scrypt verifications are capped
  (`DOKTOK_LOGIN_MAX_CONCURRENT_VERIFIES`, default 4) so login cannot exhaust the API workers.
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

## Backup and recovery operations

The same `deploy/backup.sh` / `deploy/restore.sh` run everywhere, both on a schedule (systemd
timers on the box; launchd/cron on a Mac) and manually by the system administrator on the machine:

- **Prod (docker-compose)**: `DOKTOK_DEPLOY_MODE=compose ./deploy/backup.sh` — files_root via the
  backup-runner (restic), Postgres via pgBackRest inside the db container (base + WAL archive for
  PITR). `deploy/restore.sh` restores both (DESTRUCTIVE; stops the db, restores from a one-off
  container sharing the volumes).
- **Mac dev (#745)**: the db container gets the prod pgbackrest wiring through
  `docker-compose.dev.yml` (db built from `deploy/docker/db.Dockerfile`; the backup-runner service
  under the `tools` profile). Use the Make targets, which pass the dev compose files for you:
  `make dev-backup` (or `make dev-backup TYPE=full`), `make dev-backup-pg-logical` (pg_dump safety
  net), and `make dev-restore FILES_TARGET=./storage/files` (destructive). Requires
  `DOKTOK_RESTIC_PASSWORD` and `DOKTOK_PGBACKREST_CIPHER_PASS` in `.env` (store them OFF the box;
  `DOKTOK_BACKUP_DIR` defaults to `./backups`). Backend/worker stay on the host for debugging.
- **No-container alternative (any box with the API up)**: the portable export/import via the
  settings API with the static host token (`POST /settings/backup/export`, download the encrypted
  archive, `POST /settings/backup/restore/preview` + `/apply`). That path needs no restic or
  pgBackRest at all.

DRP freshness is read-only in the UI (Settings → DRP): each leg's last run + age, from the
sentinels the scripts write into the backup dir.

### Offsite (Azure Blob, #345/#347/#348/#766)

The design is local-first: the live engine (restic + pgBackRest, minute-level PITR) works in
`./backups`, and Azure holds an **archived copy** under a **GFS rotation** (#766). Each set is
`<leg>-repo-<class>-<ts>-<fp12>.tar.gz` (class + timestamp + content fingerprint), written by
`deploy/azure-sync.sh`:

- **Rotation (code-managed, lifecycle rules can't count)**: keep 24 hourly / 7 daily / 4 weekly /
  11 monthly / 1 yearly per leg. Promotions across period boundaries are server-side COPIES of
  the newest blob (no re-upload); pruning deletes the overflow per class.
- **Two containers, container-level WORM** (version-level WORM is creation-time-only in Azure):
  `doktok-backups` holds hourly+daily (2d window), `doktok-backups-lts` holds weekly+ (30d
  window — the ransomware control).
- **Tier at write** (avoids early-deletion charges): hourly/daily Hot, weekly/monthly Cool,
  yearly Archive direct; the lifecycle ladder moves long-lived sets Cool → Cold@90 →
  Archive@180 and expires anything past 730d as the safety net.
- **Content dedup**: a leg whose newest offsite fingerprint (restic snapshot id / pgBackRest
  label+WAL max) matches the local one is skipped — a quiet week uploads nothing, and the DRP
  offsite leg compares fingerprints so it never reads as falsely stale.
- **Cost math**: pg repo plateaus at ~4–6GB after 30d retention (archive_timeout=60 forces
  ~100MB/day of WAL idle; tune via `DOKTOK_PG_ARCHIVE_TIMEOUT`, e.g. 300 for a 5min RPO).
  GFS caps at 47 sets/leg; Cool/Cold/Archive tiers keep it at single-digit €/month.

Per-instance naming is derived from `DOKTOK_INSTANCE_ID` (12 hex chars, persisted in `.env`): RG
`doktok-<id>-rg`, storage account `doktokbkp<id>`, containers `doktok-backups[/-lts]` — so
independent instances never collide (explicit `DOKTOK_AZURE_*` overrides).

Infrastructure as code (Terraform, `deploy/terraform/`; `azure-provision.sh` remains the no-TF
fallback — don't mix both on one account): RG, account (LRS, TLS1.2, no public access,
versioning, tags), both containers + immutability policies, lifecycle ladder. One-time:
`terraform init && terraform apply -var="instance_id=<id>"` with `az login`.

Credentials: an **account-level SAS with delete** (`rwdlc`, HTTPS-only, expiring) as
`DOKTOK_AZURE_SAS` in `.env` — delete is required by the code prune; the WORM windows still
protect against misuse. Store a copy OFF the box. Schedule: **daily at 03:47** (prod
`doktok-azure-sync.timer`, dev cron below; `DOKTOK_GFS_BASE_CLASS=daily` so uploads land in the
daily class — offsite RPO ~1 day, local minute-level PITR is unaffected). After each run an
**audit** counts sets per leg and flags the DRP offsite leg below `DOKTOK_OFFSITE_MIN_SETS`
(default 3).

```cron
47 3 * * *  cd <repo> && make dev-azure-sync >> backups/cron.log 2>&1
```

**Restore from Azure (#359)**: `make dev-azure-fetch` (or `deploy/azure-fetch.sh` on prod) downloads
the latest (or `TS=<ts>`) tarball pair into `./backups.azure-restore`, unpacks it, and verifies
both repos are readable. Then restore from staging with the SAME restore script:
`DOKTOK_BACKUP_DIR=./backups.azure-restore make dev-restore FILES_TARGET=./storage/files`
(+ optional `PITR=...`). The live local repo is never touched by the fetch.

The **pg leg also flags a stuck WAL archiver**:
`deploy/pg-wal-freshness.sh` (every minute in prod via `doktok-pg-wal-freshness.timer`) stamps the
sentinel with the last archived WAL time and marks the leg failed when the most recent archive
attempt failed. On the Mac dev box run it as `make dev-pg-wal-freshness` — schedule it every
minute in cron for continuous freshness (`* * * * * cd <repo> && make dev-pg-wal-freshness`).
Note: after wiping a bind-mounted backups dir, restart the db container so the mount re-resolves
(`rm -rf` of a mount point leaves a stale deleted inode inside the container and pgbackrest
crashes until the restart).

### Restore drills (proving recovery, #755)

An untested backup is not a backup. Both drills verify EVERYTHING came back — restored file count
== live count, sha256 spot-checks of restored originals against `documents.sha256`, and per-table
row counts (documents, chunks, entities, tags, notes, users, tenants, features done) compared
exactly — then write the drill sentinel so Settings → DRP shows the last drill result + measured
RPO/RTO:

- **No-risk drill (prod + dev)**: `deploy/restore-drill.sh` restores the latest files snapshot
  and pg backup into throwaway targets and compares against live — it touches NO production data.
  Prod runs it weekly (`doktok-restore-drill.timer`) and on demand (Settings → DRP → run drill);
  on the dev box: `make dev-drill`. Run it when ingestion is idle (the restored DB lags live by
  up to the WAL interval).
- **Destructive dev drill (real engine, full loop)**: `make restore-drill-dev` (asks for "wipe",
  `FORCE=1` skips) does baseline → `backup.sh` → DROP SCHEMA + wipe files_root → `restore.sh`
  with PITR to just before the wipe → verify counts/hashes + API smoke. Stop `run-backend` /
  `run-worker` first. A cron backup firing mid-drill is blocked by the anomaly guard (#747) —
  that is the guard doing its job, not a drill failure.

### Incident freeze (first response to data loss)

Post-disaster backups are poison: they snapshot the wreckage and prune the good history
(count-based retention on the pg leg, same-day collapse on the files leg). The anomaly guard
(#747) refuses to back up a database whose document count collapsed, and retention is time-based —
but when you notice data loss, do this FIRST:

1. **Stop the schedule**: `sudo systemctl stop doktok-backup.timer` (prod) or disable the cron /
   launchd entry. Every further backup makes things worse.
2. **Preserve the repo**: `cp -a backups backups.frozen-$(date +%Y%m%d-%H%M)` before any tool gets
   another chance to expire/prune it.
3. **Assess recovery points**: `pgbackrest --stanza=doktok info` (surviving backups + WAL range)
   and `restic snapshots` (in the backup-runner). Pick the newest point BEFORE the incident.
4. **Restore**: Postgres to that point-in-time (`make dev-restore FILES_TARGET=./storage/files
   PITR="YYYY-MM-DD HH:MM:SS+00"`, or `deploy/restore.sh` on prod) and files_root from the
   pre-disaster snapshot. **Always PITR for logical disasters** (DROP/DELETE): restoring "latest"
   replays the archived WAL including the destructive statement itself.
5. **Verify, then re-enable** the schedule.

Two rules, learned the hard way (2026-07-24 dev drill): never run a backup between the disaster
and the recovery; never restore "latest" for a logical disaster. If the DB recovery point is
behind the files tree (or lost entirely), reconcile with `doktok-worker rebuild-registry` (files
→ DB direction) after the tenant/users are back.

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
