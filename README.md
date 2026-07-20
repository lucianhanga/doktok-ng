# DokTok NG

Local-first, AI-enabled **document-intelligence** system. Ingests documents from local folders,
extracts text and structure, indexes them, makes them searchable, supports RAG chat with citations,
and later exposes document knowledge through a read-only MCP server.

DokTok NG is **not** a generic AI assistant. Think:

```
Paperless-ngx + local RAG + MCP + entity search
```

The first product goal is **reliable document ingestion and indexing** — not "chat with files".
Chat and MCP come later, once the ingestion foundation is solid.

Architecturally inspired by [personal-ai](https://github.com/lucianhanga/personal-ai): local-first
runtime, modular monolith, ports and adapters, contracts-first schemas, FastAPI backend, TypeScript
frontend, PostgreSQL + pgvector, Ollama model provider, security-first boundaries. DokTok NG reuses
the *style*, narrowed to documents.

## Status

**M6 (RAG chat with citations).** Ask questions about your documents at `POST /api/v1/chat` and a
**Chat** tab: the M4 hybrid retriever finds relevant chunks, the default chat model
(`DOKTOK_DEFAULT_MODEL`) answers **only from those excerpts** with `[n]` citations, and **refuses**
when the evidence is insufficient. Tenant-scoped and token-protected.

**M5 (Entity indexing).** On top of M4, ingested documents have their **entities extracted** into a
tenant-scoped `document_entities` table, browsable and filterable via `GET /api/v1/entities`
(+ `/documents`) and an **Entities** tab. The rule-based regex extractor emits **emails and URLs**
only — the low-value types (money, dates, invoice/contract/document IDs) were dropped (#312) because
their matches were ~90% noise; monetary data lives in extracted records and dates in metadata.
**PERSON/ORG/GPE** come from NER and significant keyword terms are stored as `CUSTOM_TOKEN`.

On top of the structured entities, each document's **significant terms** are extracted with
PostgreSQL `to_tsvector` in the document's **detected language** (stopwords removed, stemmed) and
stored as `CUSTOM_TOKEN` keyword entities — a multilingual lexical/keyword layer browsable and
filterable in the Entities tab (`DOKTOK_LEXICAL_TERMS_LIMIT`).

The **UI** has an Overview dashboard that separates the document **library** (Documents / Entities /
Categories counts) from an **Ingestion** pipeline section showing only actionable states (Waiting /
Processing / Failed / Pending features, or "Pipeline idle"); a **Documents** tab with **List** and
**Thumbnails** views (a shared toolbar for sort / token-filter / status / category, multi-select with
shift-range and select-all-matching, and bulk reingest/delete); a document detail card with a
two-column thumbnail + summary layout (extracted text + entities + categories + per-feature
processing + activity); a **Settings** tab to pick the AI model per purpose (see below); live
auto-refresh; and cross-linking (search hit / entity / job → open the document).

**M4 (Vector + full-text search).** On top of M3, every activated document is **chunked, embedded
(Ollama `qwen3-embedding:0.6b` → pgvector) and full-text indexed** before it goes active. **Hybrid search**
(semantic vector + Postgres FTS, fused with Reciprocal Rank Fusion) is exposed at `GET /api/v1/search`
and a **Search** tab in the UI — tenant-scoped and token-protected. Earlier milestones below.

**M3 (OCR extraction).** Files dropped into a tenant's ingest folder are detected, validated, and
**extracted into active documents**. Born-digital `.txt`/`.md`/PDF use direct/PyMuPDF extraction;
**scanned PDFs and images are OCR'd** by the configurable OCR engine (`DOKTOK_OCR_ENGINE`, default
`paddleocr` — PP-OCRv5, deterministic and CPU-only; the legacy Ollama vision model `glm-ocr` remains
selectable) and a derived `normalized/searchable.pdf` (images + invisible OCR text layer) becomes
the canonical "system document" — with the original always kept (`original.<ext>`). Mixed PDFs keep
embedded text and only OCR blank pages. **Office documents** (`.docx`/`.xlsx`/`.pptx`) are converted
to PDF on ingest by a local Gotenberg container and then follow the same path (see below). Each
document yields `manifest.json`, `content.md` (plain text for embeddings), `content.json`, and
`pages/`, surfaced via the tenant-scoped `/api/v1/documents` API and the Documents tab. Every document activity is recorded to an immutable, tenant-scoped
**activity/audit log** (`GET /api/v1/audit` and the **Activity** tab). Everything is multi-tenant and
token-protected. See the [milestone roadmap](docs/milestones/M0-M10.md).

See [`docs/architecture/doktok-ng-architecture.md`](docs/architecture/doktok-ng-architecture.md),
the [ADRs](docs/adr/), and the [milestone roadmap](docs/milestones/M0-M10.md).

## Core principles

1. Local-first; no remote AI provider by default; no network egress by default.
2. PostgreSQL + pgvector is the single first storage spine.
3. Hybrid retrieval (vector + full-text + entity), never vector-only.
4. Treat all files and extracted content as untrusted.
5. Modular monolith, ports and adapters — no premature microservices/Kubernetes/Redis/Elasticsearch/Qdrant/MinIO.
6. Every milestone produces a functional, runnable system.
7. Maintainable by one developer plus coding agents.

## Default models (Ollama, configurable)

```env
DOKTOK_DEFAULT_MODEL=qwen3.6:35b-a3b          # RAG chat / reranker (23 GB MoE)
DOKTOK_EMBEDDING_MODEL=qwen3-embedding:0.6b    # 1024-dim, no 512-token truncation
DOKTOK_EMBEDDING_NUM_CTX=1024                  # cap embedding context (chunks ~300 tok) to free KV-cache
# Enrichment + OCR-quality judge follow the Data Pipeline model selected in the UI (no hardcoded model)
DOKTOK_OCR_ENGINE=paddleocr                    # PP-OCRv5 (default); or "glm-ocr" (Ollama vision)
DOKTOK_OLLAMA_BASE_URL=http://localhost:11434
```

All model names are configurable via environment variables. See ADR-0003 (model runtime) and
ADR-0010 (OCR engine) for the rationale. The OCR runtime is an optional extra installed on the
worker host via `make ocr-paddle` (or `make ocr-rapid` / `make ocr-rapid-openvino`). These extras —
like the local reranker (`make reranker-models`) — are intentionally **not** in the lockfile, so any
`uv sync` / `uv run --frozen` prunes them; re-run the target (and restart the worker) after a sync.

The OCR-quality judge and the Ollama JSON-repair fallback both reuse the **configured pipeline
model** (no separate repair model). Pipeline and RAG reasoning each follow the reasoning density set
in Settings; the chat **Show reasoning** toggle can override the RAG reasoning per message.
`DOKTOK_EMBEDDING_NUM_CTX` caps the per-call embedding context (chunks are ~300 tokens, the model's
own default is 32k), which frees GPU KV-cache without changing the embeddings.

For throughput tuning (parallel ingestion, `OLLAMA_NUM_PARALLEL`, and the memory cost of
parallelism), see [docs/operations/performance-and-ollama.md](docs/operations/performance-and-ollama.md).

### Choosing models at runtime (Settings tab)

Beyond the environment defaults, **Settings → Model stack** lets a tenant admin pick the model
**per purpose** for their tenant — the ingestion pipeline (feature extraction), RAG / interrogation,
NER, KEG (relations) and rerank — from a catalog of local Ollama models and remote OpenAI models,
each with a reasoning-density control (`off|low|medium|high`). Resolution is **per tenant**, three
layers (epic #708): the tenant override (`PUT`/`DELETE /api/v1/settings/ai/override`) wins over the
console-global saved settings (`PUT /api/v1/settings/ai`, host token only), which win over the env
defaults; changes apply live (the backend resolves per request, the worker re-resolves on a short
interval). The OpenAI API key is **write-only and per tenant** (#719): a tenant sets its own key on
the Model stack card (stored encrypted, never read back; it wins over the console's deployment
key, which wins over the env var). Selecting an OpenAI model is an explicit, opt-in exception to
the local-first / no-egress default (ADR-0006, ADR-0014); the Ollama-only defaults keep everything
local, each tenant's `no_egress` posture is overridable in the same card, and the host
`DOKTOK_NO_EGRESS_LOCK` forces no-egress on for everyone as a floor.

The Settings AI section also shows a **read-only "Embedding (index)"** display (the embedding model
and its context window). The embedding model is intentionally **not** user-selectable: changing it
would change the vector dimension and require a schema migration plus a full re-index of every
document. OCR (engine, concurrency) is likewise deployment-global.

## Documents list and views

`GET /api/v1/documents` is **keyset-paginated** (opaque cursor) and supports:

- **Sorting** — `sort=acquired` (ingestion time, default) / `created` (the document's own date) /
  `title` / `category`, with `dir=asc|desc`.
- **Filtering** — `status`, `category`, `needs_attention`, and **token** filters (`token[]`,
  `token_match=all|any`, optional `token_type`).
- **Select-all-matching** — `GET /api/v1/documents/ids` returns every id matching the same filters
  (capped at 10k, with a `truncated` flag) so bulk actions can target the whole result set.

The Documents tab renders these as **List** (table) and **Thumbnails** (gallery) views over one shared
toolbar and selection model. Each document's first-page preview is served by
`GET /api/v1/documents/{id}/thumbnail` (a WebP produced by the reconciled `thumbnail` feature;
404 → placeholder until it has rendered).

## Supported file types

DokTok NG ingests (by content-detected MIME, not extension):

- **Text / Markdown** (`.txt`, `.md`) — direct extraction.
- **PDF** — born-digital text via PyMuPDF; scanned/mixed PDFs via OCR.
- **Images** (`.png`, `.jpg`, `.tif`, `.webp`) — OCR.
- **Office (OOXML)** (`.docx`, `.xlsx`, `.pptx`) — converted to PDF on ingest by a **local Gotenberg
  container** (`gotenberg/gotenberg:8`, MIT-licensed) that wraps headless LibreOffice, then run
  through the normal PDF path. Conversion is fully local; **document content never leaves the host**.
  Office ingestion fails with a `needs_ocr` error if Gotenberg is not reachable. (ADR-0019, #313.)

For an office document, the converted PDF is the canonical **viewable** form: in-browser preview shows
it inline like a native PDF, "Open in new tab" opens that PDF, and thumbnails / page images / the OCR
text overlay all derive from it. **Download** returns the **original** file (e.g. the `.docx`), which
is always preserved byte-for-byte. Gotenberg is configured by `DOKTOK_GOTENBERG_URL` (default
`http://localhost:3000`); override the compose host port with `DOKTOK_GOTENBERG_PORT`.

## Quickstart

Prerequisites: [`uv`](https://docs.astral.sh/uv/), [`pnpm`](https://pnpm.io/), Docker, `libmagic`
(macOS: `brew install libmagic`; Debian/Ubuntu: `apt-get install libmagic1`), and (later) Ollama.
Python 3.12 is fetched automatically by `uv`.

First copy `.env.example` to `.env` (it ships a local dev token and tenant). `make` and the app load
`.env` automatically.

```bash
# 1. Install dependencies (Python uv workspace + JS pnpm workspace)
make setup

# 2. Start the Docker services (PostgreSQL 17 + pgvector, and Gotenberg for office conversion).
#    Postgres binds host port 5433 by default (another local Postgres keeps 5432);
#    override with DOKTOK_DB_PORT if you need something else.
#    Gotenberg listens on host port 3000; override with DOKTOK_GOTENBERG_PORT if it clashes.
make db

# 3. Run the backend (http://localhost:8000)
make run-backend
#    Health is public:    curl http://localhost:8000/health
#    The API is versioned under /api/v1 and needs a token (from .env):
#    curl -H "Authorization: Bearer dev-token-default" http://localhost:8000/api/v1/ingestion/jobs

# 4. In another terminal, run the UI (http://localhost:5174).
#    Use the make target so the dev proxy injects the bearer token for you.
make run-ui

# Run the full quality gate (Python + JS)
make check
```

## Running locally / starting after a reboot

DokTok NG runs as **manual foreground processes, each in its own terminal** - there is no daemon and
no aggregate start command. After a machine reboot (or a fresh shell), start things in this order:

```bash
# 1. Start Docker Desktop (the containers do NOT auto-start with it).
# 2. Start the Docker services: PostgreSQL 17 + pgvector and Gotenberg (office -> PDF).
make db   # = docker compose up -d (starts both the db and gotenberg services)
# 3. Make sure Ollama is running (menu-bar app or `ollama serve`).

# 4. Terminal A - FastAPI backend (http://localhost:8000)
make run-backend
# 5. Terminal B - ingestion worker (auto-resumes its backlog)
make run-worker
# 6. Terminal C - UI dev server (http://localhost:5174)
make run-ui
```

Stop a foreground process with `Ctrl+C`; stop the database with `make db-down` (keeps the data).

**What persists vs. what you restart.** Across a reboot, **state persists; processes do not.** The
Postgres data (documents, embeddings, entities, chat threads, categories, audit log, and settings)
lives in the named Docker volume `doktok-pgdata`, and your files live under `storage/files/`; both
survive a reboot. What does **not** auto-start is Docker Desktop, the `doktok-db` container (no
`restart:` policy), Ollama, and the backend/worker/UI - you restart those with the commands above.
The **worker auto-resumes**: on startup it re-queues jobs a prior worker abandoned mid-pipeline and
rescans each tenant's `ingest/` folder, so ingestion continues where it left off.

Full detail - persistence model, worker auto-resume, prerequisites, and troubleshooting - is in
[docs/operations/running.md](docs/operations/running.md).

**Deploying to a small server.** To run DokTok NG on small hardware (a TRIGKEY N95: 4 cores, 8 GB, no
GPU) for staging / limited production, the local LLMs do not fit, so it uses a hybrid split - OCR and
embeddings stay local while enrichment and chat run on OpenAI or a remote LAN Ollama (ADR-0020). The
production packaging (compose, Dockerfiles, headless seeding, deploy CI) is shipped. Bootstrap a box
once with the [fresh-box runbook](docs/operations/deploy-fresh-box-runbook.md) (copy
[`.env.production.example`](.env.production.example) -> `.env.production`, fill the REQUIRED secrets),
then ship updates with the one command `make deploy-box`. For the hardware rationale, the hybrid
trade-off, and N95 tuning see
[docs/operations/deployment-trigkey-n95.md](docs/operations/deployment-trigkey-n95.md).

## Authentication, users, and multi-tenancy

DokTok NG is multi-tenant and the API is token-protected (ADR-0007, ADR-0008), with a full
tenant/user management stack on top (ADR-0024). HTTP routes are versioned under `/api/v1`
(`/health` is unversioned and public):

- Send `Authorization: Bearer <token>`; `/health` is public, `/api/v1/*` requires a credential.
- **Tenant tokens (local-first default).** Each static token maps to a tenant
  (`DOKTOK_TENANT_TOKENS` is a JSON `{"<token>": "<tenant_id>"}` map). The default `.env` ships
  two: `dev-token-default` -> tenant `default` (used by the UI dev proxy) and
  `dev-token-developer` -> tenant `developer` (for your manual API testing). Example:
  `curl -H "Authorization: Bearer dev-token-developer" http://localhost:8000/api/v1/ingestion/jobs`
  A tenant-scoped token with no user identity acts as **admin**, so a single-operator deployment
  keeps full access with zero configuration.
- **DB-backed tokens and users.** Tenants, users, and hashed revocable API tokens live in the
  database (only a token's sha256 is stored); the static map is the fallback tier. Manage them via
  the **Admin** tab or `/api/v1/admin/*` (admin role required for every call).
- **Login (opt-in).** Set `DOKTOK_AUTH_JWT_SECRET` (falls back to `DOKTOK_SECRETS_KEY`; with
  neither set, login answers 503 and tokens work unchanged) to enable
  `POST /api/v1/auth/login` (tenant + email + password -> short-lived session JWT,
  `DOKTOK_AUTH_ACCESS_TTL_SECONDS`, default 3600) and `GET /api/v1/auth/me`. With login enabled the
  UI shows a **login screen** (the SPA asks the public `GET /api/v1/auth/config` at boot and stays
  token-free otherwise); the session JWT lives in memory + sessionStorage, never localStorage or
  cookies. Passwords are hashed with stdlib scrypt (min length 12); login failures are one generic
  message.
- **Login hardening.** The login endpoint throttles brute force per account
  (`DOKTOK_LOGIN_RATE_PER_MINUTE`, default 5) and per IP (`DOKTOK_LOGIN_IP_RATE_PER_MINUTE`,
  default 20; `X-Forwarded-For` honored only with `DOKTOK_TRUSTED_PROXY=true`), caps concurrent
  password verifications, audits every attempt, and warns at startup about a weak or fallback
  signing secret.
- **Roles.** `viewer` < `editor` < `admin`: reads pass for any authenticated caller; content writes
  (ingest, entities, chat, ...) need editor; settings reads and all administration need admin.
  `make seed-dev` seeds a gated `dev` tenant with one user per role to try this locally.
- **The host console (ADR-0025, epic #700).** Deployment-spanning actions are console work, not a
  UI login: tenant provisioning (`scripts/create-tenant.sh`), backup/recovery (`deploy/backup.sh`,
  `deploy/restore.sh`), portable export/restore, DRP drills, and console-global model-stack writes
  (`PUT /settings/ai`, `PUT /settings/ocr`) accept ONLY the static host credential
  (a `DOKTOK_TENANT_TOKENS` entry, `via == "static"`) - session JWTs and user api tokens always
  get 403, and the SPA carries no platform surfaces (no Instance Administration, DRP actions, or
  console-global model-stack writes). Tenant admins keep tenant-scoped user management
  (users/roles/passwords/invitations/tokens), DRP *status* reads, and their tenant's model-stack
  override + data-egress posture (epic #708). There is no platform-admin
  user flag to grant; `make create-tenant` provisions tenants + first admins on a fresh box.
- **Invitations and deactivation.** Admins invite an email (one-time token, expiry
  `DOKTOK_AUTH_INVITE_TTL_HOURS`, default 168); the invitee accepts via
  `POST /api/v1/auth/accept-invite` to set a password. Deactivating a user blocks their session
  JWTs and API tokens on the next request - immediate revocation.
- **Per-user preferences.** UI preferences sync automatically to a server-side per-user store
  (`/api/v1/preferences`); the login-less operator gets a per-tenant bucket. No setup needed.
- All data is scoped to the caller's tenant: `tenant_id` on every table, and per-tenant filesystem
  folders. The backend binds loopback by default and fails closed if no credentials are configured.

Design and security rationale:
[ADR-0024](docs/adr/ADR-0024-tenant-user-management-and-rbac.md). Hands-on dev walkthrough (seeding,
login, curl flows for invites and tokens):
[docs/operations/running.md](docs/operations/running.md#tenant-and-user-management-in-your-dev-environment).
How the test suite covers all of this: [docs/operations/testing.md](docs/operations/testing.md).

### Ingesting documents (M1)

```bash
make db
make run-worker        # creates each tenant's lifecycle folders and runs migrations

# Drop a file into the tenant's ingest folder (default tenant shown):
cp some-document.pdf storage/files/default/ingest/
```

The worker waits until the file is stable, moves it into
`storage/files/{tenant}/in.process/{job_id}/source`, hashes it, detects its MIME type, then **extracts
content and creates an active document** under `storage/files/{tenant}/docs.active/{document_id}/`
(`.txt`/`.md`/born-digital PDF in M2). Watch progress via `GET /api/v1/ingestion/jobs` and
`GET /api/v1/documents` (with a token), or the **Ingestion** / **Documents** tabs in the UI.
Unsupported types are rejected to `.../docs.failed/`; dangerous types are isolated to
`.../quarantine/`; duplicate content (same SHA-256, per tenant) is flagged; scanned PDFs and images are
marked `needs_ocr` (handled in M3).

## Repository shape (target)

```
doktok-ng/
  contracts/                 ports, schemas, API contracts
  core/doktok_core/          domain logic: ingestion, documents, extraction,
                             indexing, retrieval, entities, security, audit
  apps/
    backend/                 FastAPI backend
    ui/                      React + Vite frontend
    worker/                  ingestion pipeline worker
    mcp/                     read-only MCP server (later)
  providers/ollama/          Ollama chat + embedding adapters
  providers/openai/          OpenAI adapters (opt-in remote provider; off by default)
  storage/postgres/          PostgreSQL adapters + migrations
  storage/filesystem/        local filesystem storage adapter
  modalities/files/          file modality handling
  retrieval/hybrid/          hybrid retrieval
  tools/builtin/             built-in tools
  tools/mcp/                 MCP tool surface
  docs/{architecture,adr,milestones,prompts}
  docker-compose.yml
  pyproject.toml
  package.json
  pnpm-workspace.yaml
  Makefile
```

## License

**PolyForm Noncommercial License 1.0.0** — see [LICENSE](LICENSE).

Free for any **noncommercial** use: personal and private use, research, education,
nonprofits, and evaluation. You may use, modify, and share it for those purposes.

**Commercial use requires a separate license.** If you want to use DokTok NG in or for
a business, contact **lucianhanga@gmx.net** — see [COMMERCIAL.md](COMMERCIAL.md).

> Note: releases and commits published before 2026-06-26 were available under the MIT
> License and remain so for anyone who obtained them under those terms. From 2026-06-26
> onward the project is licensed under PolyForm Noncommercial 1.0.0.
>
> This is a summary for convenience, not legal advice; the [LICENSE](LICENSE) text governs.
