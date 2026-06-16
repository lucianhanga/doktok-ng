# Deploying to a TRIGKEY N95 (hybrid: local OCR + embeddings, remote OpenAI enrichment + chat)

## Purpose

A practical guide to deploying DokTok NG to small hardware (a TRIGKEY N95: 4 cores, 8 GB RAM, no GPU)
for **staging** and **limited production**. The full local model stack does not fit on this box, so
this deployment uses the **hybrid split** decided in
[ADR-0020](../adr/ADR-0020-hybrid-deployment-topology.md):

- **Local on the box:** OCR (PaddleOCR), embeddings (Ollama `qwen3-embedding:0.6b`), PostgreSQL +
  pgvector, and Gotenberg (office -> PDF).
- **Remote on OpenAI:** the enrichment pipeline and RAG chat + rerank.

Read ADR-0020 first for the rationale and the privacy trade-off. This guide is the how-to.

## What is shipped today vs. planned (M11)

This guide describes a target that is **partly built**. Items marked **planned (M11: ...)** do not
exist in the repository today; do not treat them as available. Where a step has no production tooling
yet, the guide gives the manual equivalent that works today.

| Capability | Status |
|---|---|
| Per-purpose provider split (pipeline/RAG on OpenAI; OCR/embeddings local) | Shipped (ADR-0014) |
| Setting the provider split + OpenAI key via the **Settings UI** | Shipped |
| Local dev `docker-compose.yml` (db + gotenberg only) | Shipped |
| N95 tuning settings (`DOKTOK_OCR_CONCURRENCY`, `OPENAI_RECONCILE_CONCURRENCY`, ...) | Shipped |
| **Production compose** (backend + worker + ollama-embedder + db + gotenberg + Caddy) | Shipped (M11: #318) |
| **Dockerfiles** for backend, worker, and ui images | Shipped (M11: #317) |
| Per-container resource limits + restart policies | Shipped (M11: #323) |
| Caddy TLS (domain auto-HTTPS or LAN `tls internal`) + edge token injection | Shipped (M11: #321) |
| **Headless / scripted seeding** of the AI settings + OpenAI key | Shipped (M11: APP-2/#325) |
| **`DOKTOK_NO_EGRESS` blocking OpenAI** | Shipped (M11: APP-3/#326) |
| OpenAI key env fallback (`DOKTOK_OPENAI_API_KEY`) | Shipped (M11: APP-7/#320) |
| `migrate` command + advisory-locked migration | Shipped (M11: APP-1/#319) |
| Encrypted OpenAI key at rest (`DOKTOK_SECRETS_KEY`) | Shipped (M11: APP-8/#331) |
| Rate limiting, configurable CORS, body-size limit | Shipped (M11: APP-9/#332, APP-10/#333) |
| Egress/privacy indicator in the Settings UI | Shipped (M11: APP-11/#334) |
| Structured JSON logs + `/metrics`; dependency-aware `/ready`; worker heartbeat | Shipped (M11: #336/#337/#328/#329) |
| Image build/publish to GHCR + SBOM; deploy + rollback workflow | Shipped (M11: #338/#339) |
| Staging/production env profiles + security runbook | Shipped (M11: #340/#341) |
| **Outbound firewall to OpenAI only** (example provided; apply it on the host) | Partly (M11: #322) |

The M11 epic owns the full ticket list; this guide references it rather than duplicating it.

## Hardware and OS prerequisites

- **Box:** TRIGKEY N95 (Intel N95, 4 cores, 8 GB RAM, no discrete GPU). Equivalent N95/N100-class
  mini-PCs are fine.
- **OS:** **Linux preferred.** The N95 ships with Windows; install a server Linux (the project's
  tooling — Docker, `ollama serve` under systemd, the Make targets — targets Linux/macOS, and the
  performance and Ollama-server guidance in
  [performance-and-ollama.md](performance-and-ollama.md) assumes Linux systemd or macOS). Windows is
  not a supported server target here.
- **Docker** (Engine + Compose plugin) for Postgres + pgvector and Gotenberg.
- **Ollama** for the **embedding model only** on this box. Pull just `qwen3-embedding:0.6b` — do
  **not** pull the chat/enrichment models, they will not fit and are not used here.
- **`libmagic`** (MIME detection): `apt install libmagic1`.
- **PaddleOCR runtime** on the worker host: `make ocr-paddle`
  (`uv pip install paddleocr paddlepaddle pillow numpy`). Note `uv sync` prunes this extra, so re-run
  it after any dependency sync.
- An **OpenAI API key** with access to the models you will select for the pipeline and RAG purposes.

## Target topology

```
                 Internet
                    |
              (TLS 443)
                    |
        +-----------------------+        outbound HTTPS to
        |  Caddy reverse proxy  | ---------------------------> api.openai.com
        |  - terminates TLS     |        (enrichment + chat)
        |  - injects bearer     |
        |    token -> backend   |
        +-----------+-----------+
                    | (internal network, no host ports except Caddy)
        +-----------+-----------------------------------------+
        |                 internal docker network             |
        |   +---------+  +-----------+  +------------------+   |
        |   | backend |  |  worker   |  |    gotenberg     |   |
        |   +----+----+  +-----+-----+  +------------------+   |
        |        |             |                              |
        |   +----+-------------+----+   +------------------+  |
        |   |  db (pg + pgvector)   |   | ollama (embed    |  |
        |   |  volume: pgdata       |   | model only)      |  |
        |   +-----------------------+   +------------------+  |
        +-----------------------------------------------------+

   Outbound firewall: allow the box to reach OpenAI (api.openai.com) only.
```

Key points:

- **Caddy** terminates TLS and is the only component with a published host port. It injects the
  `Authorization: Bearer <token>` header toward the backend, so the bearer token is not handled by the
  browser. (This mirrors the dev UI proxy, which injects the dev token; see `make run-ui`.)
- The **backend binds loopback / internal only** and fails closed without tokens
  (`DOKTOK_TENANT_TOKENS`); it must never be exposed directly (ADR-0008). If you bind it to a
  non-loopback host, it refuses to start unless tokens are configured.
- **db, gotenberg, and the embedding-only Ollama** sit on the internal network with no host ports.
- **Egress** from the box should be firewalled to OpenAI only. OCR, embeddings, and storage are local;
  the only legitimate outbound calls are to OpenAI for enrichment and chat.

> The production compose file, the backend/worker Dockerfiles, and the Caddy config that realize this
> topology are **planned (M11)** — they are not in the repo yet. Today's `docker-compose.yml` brings up
> only `db` and `gotenberg`; the backend, worker, and UI run as host processes (`make run-backend`,
> `make run-worker`, `make run-ui`). You can stand up the hybrid split today with that local-style
> layout plus an OpenAI key; the production packaging is what M11 adds.

## Configuration (DOKTOK_* settings)

These go in `.env` (loaded by `make` and the app). All settings live in
`core/doktok_core/config.py` with inline documentation; the values below are the N95 hybrid profile.
Note these are the **environment baseline**; the AI provider split itself is stored in the database
and set via the Settings UI (next section).

### Hybrid split and egress

```env
# Egress MUST be enabled for the hybrid split: the box sends content to OpenAI. As of APP-3 the gate
# is enforced - if a purpose is set to OpenAI while NO_EGRESS=true, the app refuses to egress, logs a
# warning naming the setting, and falls back to the local model. So this must be false here
# (ADR-0006/ADR-0020). The actual outbound traffic is then restricted to OpenAI at the host firewall.
DOKTOK_NO_EGRESS=false

# Embeddings stay local on this box; point at the local embedder.
DOKTOK_OLLAMA_BASE_URL=http://localhost:11434
DOKTOK_EMBEDDING_MODEL=qwen3-embedding:0.6b   # 1024-dim; do NOT change (would force a re-index)
DOKTOK_EMBEDDING_NUM_CTX=1024
DOKTOK_EMBEDDING_KEEP_ALIVE=30m               # keep the tiny embedder resident

# OpenAI key fallback (APP-7) and headless provider-split seeding (APP-2) for a fresh DB, so the
# hybrid can be provisioned without the Settings UI. Seeding is seed-if-absent (never overwrites UI
# edits). Both are optional; the Settings UI remains the live-editable surface.
DOKTOK_OPENAI_API_KEY=sk-...
DOKTOK_PIPELINE_PROVIDER=openai
DOKTOK_PIPELINE_MODEL=gpt-4o-mini
DOKTOK_RAG_PROVIDER=openai
DOKTOK_RAG_MODEL=gpt-4o-mini
```

The OpenAI **API key** and the **pipeline/RAG provider+model selection** live in the database and are
set through the Settings UI (next section) or seeded once from the env above on a fresh deployment.

### N95 throughput tuning

```env
# OCR is the local throughput governor (PaddleOCR, CPU-bound, ~1 core/page, ~seconds/page).
# Rule of thumb: OCR_CONCURRENCY * OCR_CPU_THREADS <= physical cores. On 4 cores:
DOKTOK_OCR_CONCURRENCY=2          # 2 OCR worker processes (pages OCR'd in parallel)
DOKTOK_OCR_CPU_THREADS=1          # 1 math thread per worker (avoid oversubscription)

# How many documents flow through intake/extraction at once. Keep modest on 4 cores.
DOKTOK_INGEST_CONCURRENCY=2

# When the pipeline runs on OpenAI (remote, network-bound), the reconciler can fan out wider than the
# local default of 2. 6-8 keeps enrichment moving without flooding the API or the small DB pool.
DOKTOK_OPENAI_RECONCILE_CONCURRENCY=6   # 6-8 for this box
```

`DOKTOK_OCR_CONCURRENCY` is live-reloaded from the Settings DB between ingest scans; the env value is
the startup default.

### Local Ollama server (embeddings only)

These are environment variables on the `ollama serve` process, **not** DokTok settings (see
[performance-and-ollama.md](performance-and-ollama.md)). On this box Ollama serves only the embedding
model, so keep it lean:

```bash
# systemd: sudo systemctl edit ollama.service  -> under [Service]:
Environment="OLLAMA_MAX_LOADED_MODELS=1"   # only the embedder is ever loaded
Environment="OLLAMA_NUM_PARALLEL=2"        # a couple of concurrent embed calls; cheap at 1024 ctx
```

Pull only the embedder: `ollama pull qwen3-embedding:0.6b`. Do not pull `qwen3.6:35b-a3b` or
`qwen3:14b` — they will not fit in 8 GB and are not used in the hybrid split.

## Setting the AI provider split and OpenAI key

**Today (Settings UI):** open the **Settings** tab and, in the AI section:

1. Set the **pipeline** purpose to an **OpenAI** provider + model with a reasoning density.
2. Set the **RAG / interrogation** purpose to an **OpenAI** provider + model.
3. Enter the **OpenAI API key** (write-only: it is stored, never read back; `GET` only reports whether
   a key is set).

The selection is stored in the `app_settings` table and **applied on the next backend/worker
restart** (ADR-0014). After saving, restart the backend and worker.

The embedding model is shown read-only and is intentionally not selectable — changing it would change
the vector dimension and require a re-index (ADR-0014, ADR-0020).

**Planned (M11: APP-2):** headless / scripted seeding of the AI settings and OpenAI key so a fresh box
can be configured without clicking through the UI. This does not exist yet; use the Settings UI today.

> Security note on the key: the OpenAI API key is stored as **plaintext JSON in Postgres
> `app_settings`** today. It is write-only over the API, but database backups are therefore
> **secret-bearing**. Encryption at rest is **planned (M11: APP-8)**. Until then, protect and treat
> backups as secrets (see Backups below).

## Backups

Two things hold state; back both up:

1. **The Postgres volume** `doktok-pgdata` — documents, chunks + embeddings, entities, chat threads,
   categories, the audit log, and `app_settings` (the AI selection **and the OpenAI API key**). Back
   up with `pg_dump` against the db container, or snapshot the named volume. **This backup contains
   the OpenAI key in plaintext (until APP-8) — store it encrypted and access-controlled.**
2. **The file tree** `files_root` (`DOKTOK_FILES_ROOT`, default `./storage/files`) — originals and
   OCR/normalized output under each tenant's `docs.active/`, plus in-flight folders. Back this up as a
   normal filesystem backup.

Scripts for both are provided: [`deploy/backup.sh`](../../deploy/backup.sh) (writes a timestamped
`db.sql.gz` + `files.tar.gz`) and [`deploy/restore.sh`](../../deploy/restore.sh) (restores from such a
backup; destructive). Run a periodic `backup.sh` via cron, store the output encrypted and off-box, and
**test `restore.sh` against a staging stack** so you know the restore path works before you need it.

> **Do not run `docker compose down -v`.** The `-v` flag deletes named volumes, including
> `doktok-pgdata` — that wipes the entire database (documents, embeddings, settings, the key). Use
> `make db-down` (`docker compose down`, no `-v`) to stop the database while keeping the volume. Only
> `-v` when you intend to destroy the data.

## Privacy and security note

This deployment **sends document content and chat context to OpenAI**. This is a deliberate departure
from DokTok NG's local-first / no-egress default (ADR-0006), made because the local LLMs do not fit on
an N95 (ADR-0020). Specifically:

- With the **pipeline on OpenAI**, document text (the enrichment head, and more for some features) is
  sent to OpenAI for metadata, classification, record, and NER extraction.
- With **RAG on OpenAI**, the retrieved document chunks and the user's question are sent to OpenAI for
  the answer and the rerank.

OCR text, chunks, and embeddings are computed and stored **locally**; pgvector and the file tree never
leave the box. But the enrichment and chat paths do egress. Communicate this to stakeholders before
ingesting sensitive material, and review OpenAI's data-handling terms for your account.

As of APP-3, `DOKTOK_NO_EGRESS` **does** gate OpenAI: with it `true`, selecting OpenAI is refused and
the app falls back to the local model. The hybrid therefore requires `DOKTOK_NO_EGRESS=false` as the
explicit opt-in. If on-premises content confidentiality is a hard requirement, prefer the **separate
LAN Ollama host** alternative in ADR-0020 instead of OpenAI.

## Secrets, TLS, and the outbound firewall

- **Secrets.** Tenant tokens (`DOKTOK_TENANT_TOKENS`), the Caddy edge token (`DOKTOK_API_TOKEN`, which
  must be one of the tenant tokens), and the DB password come from an untracked `.env.production`
  (gitignored) or Docker secrets — never the `dev-token-*` defaults. The OpenAI key is entered via the
  Settings UI (persisted in Postgres) or seeded once from `DOKTOK_OPENAI_API_KEY`; rotating it is a
  Settings change + restart (or re-seed). Set `DOKTOK_SECRETS_KEY` so the key is encrypted at rest
  (APP-8); backups that include `app_settings` still carry it (the encrypted form), so keep backups
  protected. See the [security runbook](security-runbook.md) for the full exposure checklist.
- **TLS.** Caddy terminates TLS. Set `DOKTOK_SITE_ADDRESS` to a public domain for automatic
  Let's Encrypt certificates, or to an `https://` LAN host and uncomment `tls internal` in
  `apps/ui/Caddyfile` for a self-signed cert. Caddy injects the bearer token at the edge, so the
  token-free SPA never holds it. Only Caddy's 80/443 are published; all other services are
  internal-only.
- **Outbound firewall.** `DOKTOK_NO_EGRESS` gates the *app*, not the *host*. Restrict the box's
  outbound traffic to OpenAI (and DNS) with a default-deny policy — see the example at
  [`deploy/firewall-openai-only.example.nft`](../../deploy/firewall-openai-only.example.nft). This is
  the real enforcement that content leaves the host only for OpenAI.

## Related

- [Limited-production security & privacy runbook](security-runbook.md)
- [ADR-0020 — Hybrid deployment topology](../adr/ADR-0020-hybrid-deployment-topology.md)
- [ADR-0006 — Local-first, no-egress security](../adr/ADR-0006-local-first-no-egress-security.md)
- [ADR-0014 — Runtime AI model selection](../adr/ADR-0014-runtime-ai-model-selection.md)
- [Running locally / starting after a reboot](running.md)
- [Performance & Ollama tuning](performance-and-ollama.md) (the memory budget that rules out the full
  local stack)

## Last updated notes

2026-06-16. Reflects the hybrid split as buildable today (per-purpose provider via Settings UI; local
OCR + embeddings hardwired). Production compose, Dockerfiles, headless seeding (APP-2), egress flag
reconciliation (APP-3), encrypted key (APP-8), and deploy CI are M11 work and are flagged as planned,
not shipped.
