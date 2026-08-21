# DokTok NG — API Security Audit (CISO report)

- **Date**: 2026-07-17 · **Scope**: the FastAPI HTTP API (`apps/backend/doktok_api`), its auth/RBAC chain,
  domain code it invokes (`core/doktok_core`), SQL layer (`storage/postgres`), the retrieval adapter, and
  the two shipped deployment edges (`apps/ui/Caddyfile`, `docker-compose.prod.yml`, `deploy/` host helpers).
- **Method**: white-box static review by 6 parallel auditors (AuthN/sessions, AuthZ/tenancy, injection,
  DoS, crypto/secrets, hardening/egress). Every finding below was verified end-to-end in code with
  file:line evidence; overlapping findings were merged and cross-domain contradictions reconciled (noted
  where relevant). No dynamic testing was performed — see §7 caveats.
- **Threat actors**: (1) external unauthenticated, (2) authenticated viewer, (3) tenant editor/admin,
  (4) a *different* tenant's admin/user, (5) host-level.

## 1. Executive summary

The codebase is **above average in defensive quality**: SQL injection is clean everywhere audited, file
serving has consistent traversal guards, the hostile-archive restore pipeline is genuinely well built and
well tested, auth primitives (JWT pinning, scrypt, token hashing, login hardening) are correctly
implemented, and prompt-injection is treated systematically. RBAC guard wiring is complete — no viewer
can reach a write endpoint.

The weaknesses concentrate in three systemic areas:

1. **The multi-tenant authorization model.** "Every tenant admin is a deployment admin": any tenant's
   admin (or holder of any user-less tenant token, which resolves to admin by design) can export **all
   tenants' data**, trigger a **whole-deployment destructive restore**, **enable off-host egress** for
   everyone, and mutate deployment-global AI/OCR settings. These are deliberate single-operator design
   choices that become Critical/High the moment tenant admins are distinct principals. The admin API
   (ADR-0024) exists precisely to support multiple tenants — so the model now contradicts the feature set.
2. **The shipped production edge.** The prod Caddyfile injects a static tenant-admin bearer token into
   *every* API request, unconditionally overwriting any client credential. In the default topology every
   caller who can reach the published port is tenant-admin, per-user login is silently defeated, and audit
   attribution collapses to "the tenant". Documented as "trusted LAN" posture — but it is the only
   documented prod path, is plain HTTP on :80, and amplifies every authenticated finding in this report.
3. **Unauthenticated denial of service.** Body-size caps only inspect `Content-Length`, so chunked bodies
   bypass them and are fully buffered in RAM — on the pre-auth login endpoint this is a one-command
   container kill (768 MB prod limit). Rate-limiter buckets never evict and are keyed on
   attacker-controlled strings, a second unauthenticated memory-growth vector.

There is also a cluster of cost-amplification DoS (chat with no quota, 200-LLM-call GET requests,
event-loop-blocking restore validation), an egress-enforcement bypass in the settings test endpoints
(blind SSRF), and hardening debt (no security headers, viewer-readable operational data, crypto
parameter drift).

**Counts after dedup/merge: 1 Critical · 5 High · 12 Medium · 14 Low · 10 Info.**

## 2. Findings overview

| ID | Sev | Finding | Domain |
|----|-----|---------|--------|
| F-01 | **Critical** | Portable backup export gives any tenant admin a full copy of ALL tenants' data | AuthZ |
| F-02 | High | Any tenant admin can trigger a destructive whole-deployment restore | AuthZ |
| F-03 | High | Any tenant admin can silently enable off-host egress for the entire deployment | AuthZ |
| F-04 | High | Prod edge (Caddy) injects a tenant-admin token into every request — RBAC & attribution erased | Hardening |
| F-05 | High | Chunked/no-`Content-Length` bodies bypass all size caps → unauthenticated memory-DoS (pre-auth on `/auth/login`) | DoS |
| F-06 | High | Rate-limiter bucket maps grow unboundedly, keyed on attacker-controlled strings | DoS |
| F-07 | Medium | `test-openai` egresses under no-egress+lock; `test-ollama`/`warmup-ollama` are admin blind SSRF | Egress |
| F-08 | Medium | Deployment-global AI/OCR/DRP settings mutable by any tenant admin | AuthZ |
| F-09 | Medium | Any tenant admin can enumerate all tenants and provision unlimited new ones | AuthZ |
| F-10 | Medium | XFF/`TRUSTED_PROXY` footgun cluster: spoofable per-IP bucket behind appending proxies; shipped compose shares one login bucket | AuthN |
| F-11 | Medium | Restore preview blocks the async event loop on multi-GB decrypt/extract/hash | DoS |
| F-12 | Medium | Upload per-file size cap enforced *after* full in-memory read | DoS |
| F-13 | Medium | `/ready`: unauthenticated deep probes (DB+Ollama+Gotenberg+OpenAI fan-out) + infra disclosure | DoS/Info |
| F-14 | Medium | Chat: no quota/concurrency control; SSE generation not promptly cancelled on disconnect | DoS |
| F-15 | Medium | `GET /entities/merge-suggestions`: up to 200 sequential LLM adjudications @600 s timeout, failures re-paid per request | DoS |
| F-16 | Medium | `DOKTOK_SECRETS_KEY` reused across 4 crypto purposes; documented prod config also makes it the JWT key | Crypto |
| F-17 | Medium | Portable-archive encryption: openssl PBKDF2 default (10k iter) + only 8-char minimum passphrase | Crypto |
| F-18 | Medium | `GET /entities/{id}/documents`: unbounded N+1 queries, no pagination | DoS |
| F-19 | Low | Viewer-readable operational data: `/settings/*` GETs, `/metrics` (tenant-ignored), `/audit` emails+IPs | Info disc. |
| F-20 | Low | Staged plaintext export archives linger up to 24 h after download (`discard_export` is dead code) | Crypto |
| F-21 | Low | Audit coverage gaps: upload actor identity, chat thread/memory deletion, preferences, projection recompute | Audit |
| F-22 | Low | No HTTP security headers anywhere (CSP/XFO/Referrer-Policy/HSTS); plain HTTP default | Hardening |
| F-23 | Low | Deactivation check skipped in the pre-warm window after a backend restart | AuthN |
| F-24 | Low | Backup export: single-flight check-then-act race; crashed build wedges the feature permanently | DoS |
| F-25 | Low | Restore preview has no single-flight → concurrent 50 GB stagings exhaust disk | DoS |
| F-26 | Low | Keyset cursor `i:` bigint overflow escapes the "tamper → 400" contract → 500 | Validation |
| F-27 | Low | Giant numeric `Content-Length` crashes the limits middleware → 500 | Validation |
| F-28 | Low | Restore `verify_members` joins unvalidated manifest names → host file existence/content oracle | Validation |
| F-29 | Low | Host request-file consumer doesn't validate `staged_id` format → traversal into root-executed restore/`rm -rf` | Hardening |
| F-30 | Low | scrypt N=2^14 below OWASP 2^17 guidance (offline-cracking margin after DB leak) | Crypto |
| F-31 | Low | Secret redaction exists only in the JSON log formatter; default text mode has none | Secrets |
| F-32 | Low | No last-admin protection: role self-demotion unguarded (self-deactivation is guarded) | AuthZ |
| F-33 | Info | User-less tenant token = admin: no least-privilege machine credential exists | AuthZ |
| F-34 | Info | Restore apply authenticates `staged_id` possession, not the archive passphrase | AuthZ |
| F-35 | Info | Weak/short JWT secret warned about, never enforced outside dev | AuthN |
| F-36 | Info | Invitation accept has a benign double-accept race | AuthN |
| F-37 | Info | Case-sensitive email uniqueness vs case-insensitive login lookup | AuthN |
| F-38 | Info | LIKE wildcards unescaped in KG entity search + aggregate merchant match (inconsistent) | Validation |
| F-39 | Info | No length bounds on free-text query parameters (`q`, `title`, `category`, entity `query`) | Validation |
| F-40 | Info | `.env` world-readable (0644); `.env.example` gives no chmod guidance | Secrets |
| F-41 | Info | `history.jsonl` tamper-evidence is an unkeyed chain over a bounded window; DB audit rows have no integrity protection | Audit |
| F-42 | Info | Misc: maintenance sentinel fail-open; unvalidated `X-Request-ID` echo; env seed can store an egressing provider under no-egress (sinks catch it); drill cooldown reads host-writable sentinel | Hardening |

## 3. Detailed findings

### Theme 1 — Multi-tenant authorization model (the systemic issue)

**F-01 [Critical] Portable backup export gives any tenant admin a full copy of ALL tenants' data.**
`POST /settings/backup/export` + `POST .../export/{id}/download` are guarded only by
`make_write_guard(Role.ADMIN)` (`apps/backend/doktok_api/main.py:441`) — *any* tenant's admin. The build
runs `pg_dump` over the entire shared DB (`core/doktok_core/backup/export.py:230-252`) and walks the
entire `files_root` (`export.py:271-279`). One archive = all tenants' documents, entities, KG, chat
threads, the full `users` table (scrypt hashes, offline-crackable), and `app_settings` (Fernet-encrypted
OpenAI key). Download is encrypted with a passphrase *the caller chooses*. Audit lands only in the
actor's tenant log — victims get no signal. Two authenticated calls exfiltrate everything.
**Fix**: introduce a deployment-owner tier (flag on `tenants` or `DOKTOK_PLATFORM_ADMINS`) and gate
export/restore/DRP to it — or remove portable backup from the per-tenant API surface (host tooling only).

**F-02 [High] Any tenant admin can trigger a destructive whole-deployment restore.**
`POST /settings/backup/restore/{staged_id}/apply` needs only `confirm:true` + a validated `staged_id`
(`routers/settings.py:1204-1281`); validation is global, not tenant-bound. Chained with F-01: export at
T1 → data accumulates → restore at T2 wipes every tenant back to T1. Doctored archives are correctly
rejected (keyed HMAC, `core/doktok_core/backup/restore.py:276-289`) — but an admin's own legitimately
exported archive passes. Confirm-to-destroy, single-flight and the maintenance sentinel limit *how*, not
*who*. **Fix**: same platform-owner gate; consider an out-of-band host ack before the helper runs.

**F-03 [High] Any tenant admin can silently enable off-host egress for the entire deployment.**
`PUT /settings/ai` honors `no_egress=false` unless the host set `DOKTOK_NO_EGRESS_LOCK`
(`routers/settings.py:256-269`; lock defaults false, `core/doktok_core/config.py:124-127`). Per-purpose
`ollama_base_url` is shape-validated only (`settings.py:196-205`) — with egress off, an admin points
e.g. the RAG purpose at `https://attacker.example` and **every tenant's** chat prompts + retrieved
document chunks stream out (`dependencies.py:339-347` builds the model verbatim). The `EGRESS_ENABLED`
audit row lands only in the actor's tenant. **Fix**: recommend `DOKTOK_NO_EGRESS_LOCK=true` for any
multi-tenant deployment; restrict the toggle and remote-URL fields to platform-owner; mirror the warning
into every tenant's audit log.

**F-08 [Medium] Deployment-global AI/OCR/DRP settings mutable by any tenant admin.** `PUT /settings/ai`,
`PUT /settings/ocr`, `POST /settings/drp/drill` persist to the global `app_settings` table and trigger
host-level drills — cross-tenant sabotage (break the shared OCR engine, impose LLM costs, destabilize
the box). **Fix**: platform-owner gate, or document "every tenant admin is a deployment admin" as a hard
single-operator constraint.

**F-09 [Medium] Cross-tenant tenant enumeration + unlimited tenant creation.** `GET /admin/tenants`
returns all tenants (no filter, `routers/admin.py:168-170`); `POST /admin/tenants` mints new tenants with
admin bootstrap tokens, uncapped (`admin.py:182-203`). Sanctioned by ADR-0024, but the module docstring's
"cannot read another tenant's members" overstates the isolation. **Fix**: scope listing to caller's
tenant / platform-owner gate; rate-limit tenant creation.

**F-33 [Info] The user-less-token = admin pivot.** Any tenant-scoped credential without a user resolves
to `Role.ADMIN` (`dependencies.py:717-723`). There is no read-only machine credential; token theft reaches
F-01…F-03. Deliberate local-first compat — document the amplification; consider a `role` on api_tokens.

**F-32 [Low] No last-admin protection.** `set_user_role` doesn't guard self-demotion of the last active
admin (`routers/admin.py:246-268`) while `_set_status` does guard self-deactivation (`admin.py:306-310`).
**Fix**: mirror the guard for role changes.

**F-34 [Info] Restore apply authenticates `staged_id` possession, not the passphrase.** The decrypted
tree persists up to 6 h; the id is 128-bit random and unlisted, so this is defense-in-depth: bind the
validated marker to the actor or re-require the passphrase at apply.

### Theme 2 — Production edge topology

**F-04 [High] The prod edge authenticates nobody and downgrades everybody to one tenant-admin.**
`apps/ui/Caddyfile:19-24` sets `header_up Authorization "Bearer {$DOKTOK_API_TOKEN}"` on all `/api/*` —
Caddy `header_up` **overwrites** any client header. `DOKTOK_API_TOKEN` must be a static tenant token
(`docker-compose.prod.yml:237`), which resolves to admin (`dependencies.py:717-718`). Effects: (a) every
caller on the network is tenant-admin — full read/download, settings, backup export with attacker-chosen
passphrase, destructive restore; (b) even with login enabled, viewer/editor JWTs are discarded at the
edge, so per-user RBAC and audit attribution are silently inert through the only documented prod path.
Currently plain HTTP on :80 with a "trusted LAN" comment. **Fix**: inject only when the client sent no
Authorization (mirror the Vite dev proxy's conditional); make the injected token a viewer-role user-bound
token; gate the edge (basic-auth/mTLS/forward-auth) beyond trusted LANs; enable TLS by default.

**F-10 [Medium] XFF/`TRUSTED_PROXY` footgun cluster.** Three reconciled facets (two auditors disagreed;
resolution follows the Caddy docs and the shipped files):
- *Shipped compose* doesn't set `DOKTOK_TRUSTED_PROXY` → behind Caddy all clients share the proxy's
  per-IP login bucket (20/min): ~20 failed logins/min from anyone 429s **all** logins indefinitely, and
  audit IPs record the proxy (`routers/auth.py:94-102`; compose backend env).
- *Runbook-recommended config* (`TRUSTED_PROXY=true` behind the shipped Caddy) is **safe**: Caddy's
  `reverse_proxy` overwrites inbound XFF by default, so the leftmost element the backend reads
  (`auth.py:98-101`) is the real client IP.
- *Appending proxies* (nginx `proxy_add_x_forwarded_for`, or Caddy with `trusted_proxies`) leave element
  [0] client-controlled → rotating spoofed XFF defeats the per-IP spray bucket (per-account bucket still
  holds). **Fix**: key on the rightmost XFF element or have Caddy emit `X-Real-IP`; wire
  `DOKTOK_TRUSTED_PROXY=true` into the prod compose + uvicorn `--proxy-headers`; regression-test spoofed
  XFF.

### Theme 3 — Denial of service

**F-05 [High] Chunked/no-`Content-Length` bodies bypass every size cap → unauthenticated memory-DoS.**
The 413 pre-check only fires when `Content-Length` exists and is numeric (`main.py:222-232`); Starlette
then buffers the whole body in RAM before pydantic bounds apply. Reachable **pre-auth** via
`POST /api/v1/auth/login` and `/auth/accept-invite`. A few GB trickled chunked → the 768 MB backend
container is OOM-killed; repeat after `restart: unless-stopped` for a sustained outage. Authenticated
callers can do the same to `/api/v1/chat` etc. The restore preview is *not* affected (byte-counted
streaming cap). **Fix**: enforce the cap on actual bytes read (wrap `request.stream()`), or reject
non-GET requests without a valid numeric `Content-Length` on non-exempt paths; add a Caddy
`request_body { max_size }` as defense-in-depth.

**F-06 [High] Rate-limiter buckets never evicted; keys attacker-controlled.**
`ratelimit.py:19-36` — the dict only grows. (a) Login per-account bucket key is
`acct:{tenant}:{email}` from the request body, and `LoginRequest.email/tenant_id` have **no max_length**
(`routers/auth.py:48-53,108-113`) → 20 requests/min × ~24 MB unique emails ≈ **480 MB/min** of permanent
heap from one IP (throttles are default-on). (b) The API limiter (default off) is keyed on the raw bearer
token pre-auth (`main.py:236-239`) → unique garbage tokens grow it forever when enabled. **Fix**: hash or
length-cap bucket keys (`max_length=320` on login fields); add idle-TTL eviction / max-size bound; key
the API limiter on resolved identity or a token hash with a shared unknown-token bucket.

**F-11 [Medium] Restore preview blocks the event loop.** `async def preview_backup_restore` calls
`validate_staged_upload` synchronously (`routers/settings.py:1110,1168`) — minutes of openssl + hashing
(up to 50 GB default cap) stall the single uvicorn loop for **all** tenants, `/health` included; failed
probes can restart the container mid-validation. **Fix**: `run_in_threadpool` or background task + status
poll; add preview single-flight.

**F-12 [Medium] Upload per-file cap enforced after full read.** `data = await upload.read()` then size
check (`routers/ingestion.py:68-74`); the path budget is 2560 MB, so a ~2.5 GB file is fully RAM-resident
before rejection (chunked removes even that ceiling). **Fix**: stream to `.part` in bounded chunks, abort
+ delete at the cap (the restore preview already implements this pattern).

**F-13 [Medium] `/ready` deep probes + disclosure.** Unauthenticated, rate-limit-exempt, proxied
publicly: each call = DB checkout + Ollama `/api/tags` + Gotenberg probe (+ OpenAI probe) — ~4-7 s of
threadpool pinning when a dependency hangs; ~40 concurrent requests saturate the pool. Failure detail
strings leak internal host/IP/port and provider topology (`main.py:280-375`). **Fix**: cache readiness a
few seconds / shallow unauthenticated probe / per-IP rate limit; static detail strings.

**F-14 [Medium] Chat cost & zombie generations.** No quota/concurrency cap; one request = up to ~16
model calls in multi mode. Disconnected SSE streams keep generating server-side (pull-driven threadpool
consumption never closes the provider stream; up to 120 s read timeout during silent prefill). Prod runs
`OLLAMA_NUM_PARALLEL=2` — a few fire-and-close streams stall all tenants' chat; on OpenAI it's direct
spend. **Fix**: global/per-tenant generation semaphore; break the SSE loop on `is_disconnected()` so the
httpx context closes and Ollama aborts.

**F-15 [Medium] merge-suggestions LLM fan-out.** First-call `GET /entities/merge-suggestions?limit=200`
runs up to 200 sequential adjudications at the *ingestion* 600 s timeout with no negative caching —
failures are re-paid on every request; a few requests exhaust the 40-thread pool. **Fix**: interactive
timeout, cap adjudications per request (page the rest), cache error verdicts briefly.

**F-18 [Medium] `GET /entities/{id}/documents` unbounded N+1** — no LIMIT on mentions + per-document
fetch loop (`routers/entities.py:341-362`). **Fix**: paginate; batch-fetch `id = ANY(%s)`.

**F-24 [Low]** Export single-flight race (concurrent POSTs all pass the check → parallel pg_dumps) and a
crashed build leaves a `building` status that is never swept → feature wedged until manual deletion
(`routers/settings.py:886-897`; `core/doktok_core/backup/export.py:339-355`). **Fix**: write `building`
synchronously (create-exclusive); sweep stale building statuses.

**F-25 [Low]** Restore preview has no single-flight → concurrent ≤50 GB stagings exhaust the volume.
**Fix**: single-flight like export.

**F-27 [Low]** Giant numeric `Content-Length` (>4300 digits) raises in `int(cl)` → unhandled 500 in
middleware (`main.py:231`). **Fix**: length-check before `int()`.

**F-42 (partial)** and the INFO-tier DoS notes (CPU-burning page-image rasterization, reprocess-all
fan-out pinning a thread for minutes) are bounded per-call but only throttled by the default-off limiter
— fold into the F-06/F-14 remediation (enable + fix the limiter).

### Theme 4 — Egress enforcement

**F-07 [Medium] `test-openai` bypasses no-egress (even host-locked); `test-ollama`/`warmup-ollama` are
admin blind SSRF.** `_probe_openai` runs with no egress check (`routers/settings.py:442-473`);
`test-ollama`/`warmup-ollama` probe a caller-supplied URL validated for shape only
(`settings.py:196-205,360,393`) — a connect/timeout/status oracle plus up to 200 chars of reflected error
body (`settings.py:405`) against arbitrary internal targets (e.g. `http://169.254.169.254/`, LAN
services), on a box whose selling point is "no egress". **Fix**: gate all three probes behind
`effective_no_egress`/`purpose_requires_egress`; restrict test URLs to loopback when no-egress is on.

The egress core itself is solid: strict loopback parser, startup refusal of remote Ollama URLs under
no-egress, fail-closed `EgressBlocked` sinks in worker and backend, audited opt-in (see §5).

### Theme 5 — Crypto & secrets

**F-16 [Medium] One key, four purposes.** `DOKTOK_SECRETS_KEY` derives the at-rest Fernet key (bare
`sha256(key)`, `storage/postgres/doktok_storage_postgres/crypto.py:23-27`), signs session JWTs when
`DOKTOK_AUTH_JWT_SECRET` is unset (the documented prod template never mentions that var —
`.env.production.example:42`), HMACs backup manifests, and produces the archive-carried
`secrets_key_fingerprint` = HMAC over a *known fixed label*. Any captured JWT or backup archive is an
offline brute-force oracle against a weak key; one key compromise collapses all four domains. Startup
warns loudly; prod template instructs `openssl rand -hex 32` (then infeasible). **Fix**: HKDF
purpose-separated subkeys; add `DOKTOK_AUTH_JWT_SECRET` to `.env.production.example`; refuse the fallback
outside dev.

**F-17 [Medium] Archive encryption: openssl PBKDF2 default 10k iterations + 8-char minimum passphrase.**
`core/doktok_core/backup/export.py:315-336` uses `-pbkdf2` without `-iter` (default 10000, ~60× below the
OWASP 600k recommendation); passphrase policy `min_length=8` (`routers/settings.py:818,1115`). The
archive is the highest-value artifact in the system (full DB incl. password hashes + all files); typical
human passphrases fall to GPU attacks. A test pins the current argv — i.e. pins the weakness. **Fix**:
`-iter 600000 -md sha256`; decrypt tries new count then falls back; raise min length / encourage
generated passphrases.

**F-20 [Low] Staged plaintext archives linger after download.** `discard_export` has zero call sites;
the plaintext `.tgz` stays until the 24 h TTL sweep (`routers/settings.py:958-995`,
`core/doktok_core/backup/export.py:358-361`). **Fix**: call it in a `finally` after streaming.

**F-30 [Low] scrypt N=2^14** (~16 MiB) vs OWASP 2^17 guidance — ~8× cheaper offline cracking after a DB
leak. Self-describing hashes make raising it non-breaking. **Fix**: raise N (verifies are
semaphore-capped; the box has 8 GB).

**F-31 [Low] Redaction only in JSON log mode.** Text mode (the default) installs a plain formatter; the
pattern set covers only `sk-`/`Bearer` (`core/doktok_core/logging_setup.py:21,45-53`). Latent — no
current code logs secrets. **Fix**: redact in a root-handler `Filter`; extend patterns (DSNs, JWTs).

**F-40 [Info]** `.env` observed 0644; `.env.example` lacks a `chmod 600` instruction (the prod template
has one).

**F-41 [Info]** `history.jsonl` is an unkeyed hash chain verified over a 256 KiB tail (corruption
detection, not tamper-proofing — a host attacker can recompute it; tail deletion passes); DB audit rows
have no integrity protection. Consider keying the chain with a derived subkey and anchoring the head into
the DB.

### Theme 6 — Validation, disclosure & hardening

**F-19 [Low] Viewer-readable operational data.** Settings GETs pass the write guard for any authenticated
caller: `/settings/drp` (host backup path, Azure container, key-presence flags, cadence), `/settings/ai`
(per-purpose URLs/models, egress state), OCR hardware recommendation, export/restore status
(`routers/settings.py` 217-236, 507-607, 938-955). `/api/v1/audit` exposes all users' login emails + IPs
+ admin actions to viewers (`routers/audit.py:18-26`; login rows store email+IP, `routers/auth.py:144`).
`/metrics` takes a tenant and ignores it — any tenant's credential reads deployment-global backup
ages/heartbeat (`main.py:377-409`). **Fix**: admin-gate settings reads + `/metrics`; editor-gate or
field-strip `/audit`.

**F-21 [Low] Audit coverage gaps.** No `record_activity` for: API upload (the uploader's identity is
never recorded — the worker later logs with `actor="worker"`), chat thread create/rename/delete/truncate,
memory deletion, preference writes, projection recompute. After a malicious-document incident you cannot
answer "who uploaded this". **Fix**: audit at API upload time with `actor_identity(tenant)`; add
deletion events.

**F-22 [Low] No security headers** (CSP, X-Frame-Options/frame-ancestors, Referrer-Policy, HSTS) in
backend or Caddyfile — clickjacking exposure for an SPA whose destructive actions ride the edge-injected
token (no cookies, so SameSite doesn't help); plain-HTTP default compounds it. **Fix**: small header
middleware or Caddy `header` block; HSTS with TLS.

**F-23 [Low] Deactivation pre-warm window.** The `TenantRegistry` is built lazily on the first
auth/admin request; until then the per-request deactivation check is skipped (`dependencies.py:103-110,
139-162`) — a deactivated user's unexpired JWT (≤1 h TTL) keeps *read* access after a backend restart on
a quiet box. Role fails closed to viewer. **Fix**: register eagerly at startup when a DB is configured.

**F-26 [Low] Cursor overflow 500.** Keyset cursor `i:` values aren't range-checked; `::bigint` cast
overflows in Postgres → unhandled 500, breaking the "tamper → 400" contract (`routers/documents.py:135`;
`repositories.py:281-292,507`). **Fix**: int64 range check in `_decode_cursor`.

**F-28 [Low] Manifest-name path oracle.** `verify_members` joins unvalidated manifest names onto the
extraction root (`core/doktok_core/backup/restore.py:259`); absolute/`..` names escape, and the HMAC
failure doesn't short-circuit it → booleans leak host file existence/content via the preview `errors`
list. Content never returned; apply unreachable. **Fix**: reject absolute/traversal member names; skip
`verify_members` when HMAC failed.

**F-29 [Low] Host request-file consumer doesn't validate `staged_id`.** `deploy/restore-import.sh:28-36`
interpolates it unvalidated; a request-file writer (backend-uid host attacker — **not** API-reachable;
the API path is validated) gets a root-executed restore of an attacker tree + `rm -rf` on the traversed
dir. **Fix**: validate `^[0-9a-f]{32}$` in the unit/script; `realpath` containment check.

**F-37 [Info]** Case-sensitive `UNIQUE(tenant_id,email)` vs case-insensitive login lookup — concurrent
admin creates can produce case-variant duplicates; identity ambiguity. Fix: unique index on `lower(email)`.

**F-35/F-36/F-38/F-39/F-42 [Info]** Weak-JWT-secret warning not enforced (refuse outside dev); benign
invite double-accept race; unescaped LIKE wildcards in KG search/merchant match (consistency); no
`max_length` on free-text query params; maintenance sentinel fail-open by design; unvalidated
`X-Request-ID` echo (cap charset/length); env seed can persist an egressing provider under no-egress
(sinks catch it — align the bootstrap with the PUT boundary).

## 4. Unconfirmed suspicions (need dynamic or deeper verification)

- **SSE zombie-generation lifetime** — statically nothing closes an abandoned stream; confirm by
  disconnecting mid-prefill and watching `ollama ps`.
- **Upload filename with NUL/newline** — `_safe_filename` rejects only `.`/`..`/slashes; a NUL likely
  500s, a newline lands in logs/audit. One multipart unit test confirms.
- **`read_export_status` path join** with query-supplied `export_id` (unlike the download path param it
  may contain `/`) — no realistic target exists (only app-created `*.status.json` parse); validate the id
  format as hardening.
- **Symlink plant in restore staging → worker-side file read** — the API file-serving layer rejects
  symlink escapes, but worker artifact reads were not verified for containment (host-attacker scenario).
- **TOCTOU on file-serving guards** — resolve-then-read without holding the path (host-attacker scenario).
- **Chunked-upload disk spooling** — Starlette should spool large multipart to disk; if so, chunked
  uploads are a disk-exhaustion vector before the in-memory route read.
- **Mid-process auth-tier flip** — a credential present in both the DB (revoked) and the static env map
  keeps working via the static fallback until/unless the registry activates. Contrived; noted.
- **Cross-tenant category link injection** — `set_document_categories` doesn't verify category tenancy;
  not reachable via any API today (only worker writes links); a future caller-chosen path would leak
  cross-tenant category names.
- **Host-sentinel free-text `detail` → UI** — React escapes by default; confirm no attribute/URL
  interpolation in the DRP panels.

## 5. Verified solid (scope coverage — checked and correctly implemented)

- **SQL injection**: clean everywhere audited. All user values bound; every f-string in SQL is a constant
  or enum-whitelist lookup; LIKE escaping deliberate; aggregation fully parameterized typed-intent.
- **Auth primitives**: constant-time full-map static-token sweep; sha256-only DB tokens (256-bit entropy,
  plaintext shown once, indexed revocation); JWT pinned to HS256 with `typ`+manual `exp`+required claims;
  no role claim — roles resolved server-side per request, fail-closed to viewer; per-request deactivation;
  scrypt with per-hash params, constant-time verify, decoy-hash anti-enumeration, throttle-before-work,
  verify semaphore; invitations 256-bit/single-use/expiring/generic-errors; password hash never serialized
  on read paths; fail-closed unconfigured-auth (503, loopback-only without tokens).
- **RBAC wiring**: all 17 routers read — every handler authenticated except the five public-by-design
  routes; no viewer-reachable write endpoint; admin router gated on all methods; tenant always from the
  credential (never request input; agent tools get it server-side).
- **Tenant isolation in SQL**: all 18 repositories verified — every by-id read/mutate filters
  `tenant_id`; retriever scopes both legs; cross-tenant IDOR tests exist at the right seams.
- **File serving**: resolve-and-contain guards on every path; tenant-scoped 404 before path building;
  `nosniff`; upload basename sanitization + `.part` atomic publish; content-sniffed MIME downstream.
- **Portable restore pipeline** (the dangerous feature): independent hostile-archive validator
  (absolute/traversal/symlink/hardlink/special rejected; entry/size/ratio caps pre-extraction) +
  `filter="data"` second layer; per-member sha256 re-verify; keyed manifest HMAC; pg-major/schema gates;
  confirm-to-destroy; validated-marker; single-flight; streamed byte-counted upload cap; request file
  carries ids only (0600); passphrase via openssl stdin only, never argv/logs; maintenance sentinel parks
  writes; root helper deletes the request first, flocks, rate-limits; mandatory safety snapshot + rollback.
  17 core + 14 API tests.
- **Prompt injection**: systematic "data, not instructions" fencing in RAG/tools/providers; `[n]` marker
  neutralization against forged citations; read-only tenant-scoped tools validated at a gateway; bounded
  agent loop (6 iterations + forced close); closed relation-predicate vocabulary with grounding checks.
- **OpenAI key hygiene**: write-only end-to-end (`openai_api_key_set` boolean only); Fernet-encrypted at
  rest; never in responses/logs/audit; decrypt errors don't embed it.
- **Egress core**: strict loopback URL parser; startup refusal of remote Ollama under no-egress; PUT
  boundary validates against the new posture incl. host lock; fail-closed `EgressBlocked` sinks in worker
  and backend builders; audited opt-in.
- **DoS controls that hold**: correct token-bucket math; login throttle ordering; input bounds on chat
  (4000 chars/40×8000/limit≤20); agent loop caps; documents/ids 10k cap; keyset pagination ≤200 with
  batched sidecars (no N+1 on the list); KG neighborhood depth/edge caps; metrics low-cardinality,
  token-gated, never raises; no request-driven ReDoS surface found.
- **Error hygiene**: no catch-all leaking internals; curated `detail=str(exc)` only for domain errors;
  host paths suppressed in drill/restore queueing; DSN fragments stripped from export errors.

## 6. Prioritized recommendations

**P0 — decide the tenancy contract, then close the model gap (this week):**
1. Decide explicitly: single-operator appliance (then document "every tenant admin = deployment admin"
   loudly and downgrade F-01…F-03 to accepted risk) **or** real multi-tenancy (then implement a
   platform-owner tier and gate backup export/restore, DRP, egress toggle, global settings, tenant
   provisioning — F-01, F-02, F-03, F-08, F-09).
2. Fix the prod edge regardless: conditional token injection (only when absent), viewer-role injected
   token, TLS + an edge-auth story beyond "trusted LAN" (F-04); wire `DOKTOK_TRUSTED_PROXY` (F-10).
3. Close the unauthenticated DoS pair: streaming body caps (F-05) and bounded/hashed rate-limiter keys
   + login field `max_length` (F-06).

**P1 — robustness of expensive endpoints (next sprint):** restore-preview threadpool offload +
single-flight (F-11, F-25); streaming upload cap (F-12); `/ready` caching/shallow probe + redacted detail
(F-13); chat concurrency semaphore + disconnect abort (F-14); adjudication timeout/cap/negative cache
(F-15); entities documents pagination (F-18); export race/stale-building sweep (F-24); egress-gate the
test endpoints (F-07).

**P2 — hardening program (backlog):** HKDF key separation + JWT-secret separation in the prod template
(F-16, F-35); PBKDF2 `-iter 600000` + passphrase policy (F-17); `discard_export` after download (F-20);
scrypt N=2^17 (F-30); admin-gate settings reads + `/metrics`, editor-gate/strip `/audit` (F-19); audit
the upload actor + deletion events (F-21); security headers (F-22); eager TenantRegistry (F-23);
last-admin guard (F-32); validation nits (F-26…F-29, F-37…F-39); log redaction filter (F-31);
`staged_id` format check in the host helper (F-29); audit-chain keying (F-41).

**Process:** add regression tests for every fixed finding (the suite is strong — keep the convention);
add the Caddyfile and compose env to test coverage (currently zero tests touch the proxy layer, where
F-04/F-10 live); consider a security section in CI (chunked-body and bucket-growth tests are cheap).

## 7. Method & caveats

- Static white-box review only: no service was started, no payload executed. Findings carry file:line
  evidence verified by the auditors; the §4 list is explicitly unproven.
- Two auditor disagreements were reconciled against primary sources during synthesis: the XFF/Caddy
  interaction (F-10 — resolved per Caddy's documented default of overwriting inbound XFF) and the
  severity framing of the tenant-admin findings (F-01…F-03 — reported at multi-tenant severity because
  ADR-0024 ships multi-tenant administration; they are accepted design risk only under a strict
  single-operator contract — the decision is explicitly called out as P0-1).
- Severity assumes the shipped prod topology where relevant (edge token injection makes "authenticated"
  findings reachable by anyone on the network — noted per finding).
- Not in scope: the worker's internal file handling (one suspicion logged), the MCP server beyond its
  config surface, the UI beyond proxy/session behavior, dependency CVE scanning (uv.lock audit).
