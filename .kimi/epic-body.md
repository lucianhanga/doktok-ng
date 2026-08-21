Epic for the remediation backlog of the **2026-07-17 white-box API security audit** (CISO review of the FastAPI surface: 6 parallel domain audits — AuthN/sessions, AuthZ/tenancy, injection, DoS, crypto/secrets, hardening/egress — every finding verified end-to-end in code with file:line evidence).

**Posture summary:** defensive baseline is strong (SQLi clean everywhere audited, file-serving traversal guards consistent, the hostile-archive restore pipeline is genuinely well-built and well-tested, auth primitives correct, prompt-injection systematically fenced, tenant isolation in SQL verified across all 18 repositories with IDOR tests). The weaknesses are systemic, not coding slips.

**Findings: 1 Critical · 5 High · 12 Medium · 14 Low · 10 Info** (42 tickets below, deduplicated/merged across domains).

### Decision needed first (P0 gate)

The severe findings (F-01…F-03, F-08, F-09) all stem from one model choice: **"every tenant admin is a deployment admin."** That is coherent for a single-operator appliance, but ADR-0024 (#523) ships multi-tenant administration, so the model now contradicts the feature set. Decide:

- **Single-operator** → document "every tenant admin = deployment admin" loudly, downgrade F-01/F-02/F-03 to accepted design risk, keep the rest; or
- **Multi-tenant** → introduce a platform-owner tier and gate backup export/restore, DRP, the egress toggle, global AI/OCR settings, and tenant provisioning behind it.

Everything in P0 is independent of that decision except where noted.

### P0 — tenancy model, prod edge, unauthenticated DoS
- [ ] #613 portable backup export lets any tenant admin exfiltrate ALL tenants' data (Critical)
- [ ] #614 any tenant admin can trigger a destructive whole-deployment restore
- [ ] #615 any tenant admin can silently enable off-host egress for the entire deployment
- [ ] #616 prod Caddy edge injects a tenant-admin token into every API request (RBAC + attribution erased)
- [ ] #617 chunked bodies bypass all size caps — unauthenticated memory DoS on `/auth/login`
- [ ] #618 rate-limiter bucket maps grow unboundedly on attacker-controlled keys
- [ ] #619 deployment-global AI/OCR/DRP settings mutable by any tenant admin
- [ ] #620 any tenant admin can enumerate all tenants + provision unlimited new ones
- [ ] #621 XFF/TRUSTED_PROXY footguns — spoofable or shared per-IP login bucket

### P1 — robustness of expensive endpoints
- [ ] #622 settings test endpoints bypass no-egress + allow blind SSRF
- [ ] #623 restore preview blocks the event loop on multi-GB validation
- [ ] #624 upload per-file size cap enforced after full in-memory read
- [ ] #625 `/ready` unauthenticated deep probes + infrastructure disclosure
- [ ] #626 chat has no quota/concurrency control; SSE zombies on disconnect
- [ ] #627 merge-suggestions GET can run 200 sequential LLM adjudications
- [ ] #628 `GET /entities/{id}/documents` is an unbounded N+1
- [ ] #629 backup export single-flight race + permanently wedged after crash
- [ ] #630 restore preview has no single-flight — concurrent 50 GB stagings

### P2 — hardening & hygiene
- [ ] #631 split DOKTOK_SECRETS_KEY into per-purpose subkeys (HKDF)
- [ ] #632 raise backup archive KDF to PBKDF2-600k + passphrase policy
- [ ] #633 admin-gate settings/metrics reads; strip emails+IPs from viewer audit
- [ ] #634 discard staged plaintext export archive after download
- [ ] #635 audit upload actor + chat/memory/preference deletions
- [ ] #636 add HTTP security headers (CSP/XFO/Referrer-Policy/HSTS)
- [ ] #637 deactivated users keep read access in post-restart pre-warm window
- [ ] #638 keyset cursor bigint overflow returns 500 instead of 400
- [ ] #639 giant numeric Content-Length crashes the limits middleware
- [ ] #640 restore verify_members allows manifest-name path probing
- [ ] #641 validate staged_id format in the host restore-import helper
- [ ] #642 raise scrypt work factor to N=2^17
- [ ] #643 apply secret redaction to the text log formatter + widen patterns
- [ ] #644 guard last-admin self-demotion on role change
- [ ] #645 least-privilege machine tokens (role field on api_tokens)
- [ ] #646 bind restore apply to the previewing actor / re-require passphrase
- [ ] #647 refuse a weak JWT secret outside dev
- [ ] #648 make invitation accept a single transaction
- [ ] #649 unique index on (tenant_id, lower(email)) for users
- [ ] #650 escape LIKE wildcards in KG search + aggregate merchant match
- [ ] #651 add max_length to free-text query parameters
- [ ] #652 document/enforce chmod 600 on .env
- [ ] #653 key the backup history hash chain + anchor the head into DB audit
- [ ] #654 misc hardening — request-id validation, maintenance sentinel cache, seed egress check

### Notes

- Each ticket carries severity, threat actor, file:line evidence, exploit scenario, and remediation; tickets are labeled `security` + component + `priority-p0/p1/p2`.
- Full audit report (method, verified-solid scope coverage, unconfirmed suspicions): `.kimi/security-audit-api.md` in the repo checkout (local file, not committed).
- Convention per `docs/operations/testing.md`: add a regression test with every fixed finding. The proxy layer (Caddyfile, compose env) currently has zero test coverage — #616/#621 should change that.
- Unconfirmed suspicions needing dynamic verification (not ticketed): SSE zombie-generation lifetime, NUL/newline upload filenames, symlink plants in restore staging (worker-side), TOCTOU on file-serving guards, chunked-upload disk spooling, mid-process auth-tier flip with duplicate static/DB tokens, cross-tenant category-link injection (no API path today), host-sentinel free-text into the UI.
