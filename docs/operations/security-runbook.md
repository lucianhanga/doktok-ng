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
- **OCR and embeddings stay local** (PaddleOCR + the Ollama embedder); pgvector and the file tree
  never leave the box.

`DOKTOK_NO_EGRESS=true` blocks OpenAI entirely (APP-3): the system refuses to egress and falls back
to the local model. The hybrid requires `DOKTOK_NO_EGRESS=false` as the explicit opt-in. The actual
outbound traffic is then constrained at the host firewall, not in the app. Communicate this to
stakeholders before ingesting sensitive material, and review OpenAI's data-handling terms. If
on-prem confidentiality is required, use the separate-LAN-Ollama-host option in ADR-0020 instead.

## Exposure checklist (before going live)

- [ ] TLS enforced: `DOKTOK_SITE_ADDRESS` is a domain (auto-HTTPS) or an `https://` host with
      `tls internal`; nothing is served on plain HTTP to untrusted networks.
- [ ] Tenant tokens rotated off the `dev-token-*` defaults; long and random. `DOKTOK_API_TOKEN`
      (the Caddy edge token) is one of `DOKTOK_TENANT_TOKENS`.
- [ ] `DOKTOK_SECRETS_KEY` set so the OpenAI key is encrypted at rest (APP-8).
- [ ] Outbound firewall: default-deny, allow only 443 to `api.openai.com` + DNS
      (`deploy/firewall-openai-only.example.nft`).
- [ ] Only Caddy publishes host ports (80/443); db, ollama, gotenberg, backend are internal-only.
- [ ] Rate limiting on (`DOKTOK_RATE_LIMIT_PER_MINUTE` > 0) and `DOKTOK_LOG_FORMAT=json`.
- [ ] Backups run on a schedule, encrypted and off-box; restore tested against staging (DEVOPS-6).
- [ ] No secrets in images or logs (the JSON logger redacts keys/bearer tokens; images are built
      from a `.dockerignore` that excludes `.env*`).

## Incident response

**Suspected OpenAI key exposure**
1. Revoke the key in the OpenAI dashboard.
2. Set a new key via Settings -> AI (or update `DOKTOK_OPENAI_API_KEY` and re-seed), then restart
   the backend + worker.
3. Review OpenAI usage for anomalies.

**Suspected tenant-token exposure**
1. Replace the affected token in `DOKTOK_TENANT_TOKENS` (and `DOKTOK_API_TOKEN` if it was the edge
   token); restart the backend and Caddy.
2. Old tokens stop working immediately on restart (tokens are validated against the configured map).

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
