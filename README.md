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

**M5 (Entity indexing).** On top of M4, ingested documents have their **entities extracted** (rule-based:
emails, URLs, money, dates, invoice/contract IDs) into a tenant-scoped `document_entities` table, browsable
and filterable via `GET /api/v1/entities` (+ `/documents`) and an **Entities** tab. (PERSON/ORG NER via
spaCy is a documented follow-up.)

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
embedded text and only OCR blank pages. Each document yields `manifest.json`, `content.md` (plain text
for embeddings), `content.json`, and `pages/`, surfaced via the tenant-scoped `/api/v1/documents` API
and the Documents tab. Every document activity is recorded to an immutable, tenant-scoped
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
DOKTOK_ENRICH_MODEL=qwen3:14b                  # dense; enrichment + OCR-quality judge + JSON repair
DOKTOK_OCR_ENGINE=paddleocr                    # PP-OCRv5 (default); or "glm-ocr" (Ollama vision)
DOKTOK_OLLAMA_BASE_URL=http://localhost:11434
```

All model names are configurable via environment variables. See ADR-0003 (model runtime) and
ADR-0010 (OCR engine) for the rationale. PaddleOCR needs its optional extra installed on the worker
host: `uv pip install paddleocr paddlepaddle`.

The OCR-quality judge and the Ollama JSON-repair fallback both reuse the **configured pipeline
model** (no separate repair model). Pipeline and RAG reasoning each follow the reasoning density set
in Settings; the chat **Show reasoning** toggle can override the RAG reasoning per message.
`DOKTOK_EMBEDDING_NUM_CTX` caps the per-call embedding context (chunks are ~300 tokens, the model's
own default is 32k), which frees GPU KV-cache without changing the embeddings.

For throughput tuning (parallel ingestion, `OLLAMA_NUM_PARALLEL`, and the memory cost of
parallelism), see [docs/operations/performance-and-ollama.md](docs/operations/performance-and-ollama.md).

### Choosing models at runtime (Settings tab)

Beyond the environment defaults, the **Settings** tab (`GET`/`PUT /api/v1/settings/ai`) lets you pick
the model **per purpose** — the ingestion pipeline (feature extraction) and RAG / interrogation — from
a catalog of local Ollama models and remote OpenAI models, each with a reasoning-density control
(`off|low|medium|high`). The choice is stored as global system settings (`app_settings` table) and
applied on the next backend/worker restart. The OpenAI API key is **write-only** (set or cleared via
the API, never read back). Selecting an OpenAI model is an explicit, opt-in exception to the
local-first / no-egress default (ADR-0006, ADR-0014); the Ollama-only defaults keep everything local.

The Settings AI section also shows a **read-only "Embedding (index)"** display (the embedding model
and its context window). The embedding model is intentionally **not** user-selectable: changing it
would change the vector dimension and require a schema migration plus a full re-index of every
document.

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

## Quickstart

Prerequisites: [`uv`](https://docs.astral.sh/uv/), [`pnpm`](https://pnpm.io/), Docker, `libmagic`
(macOS: `brew install libmagic`; Debian/Ubuntu: `apt-get install libmagic1`), and (later) Ollama.
Python 3.12 is fetched automatically by `uv`.

First copy `.env.example` to `.env` (it ships a local dev token and tenant). `make` and the app load
`.env` automatically.

```bash
# 1. Install dependencies (Python uv workspace + JS pnpm workspace)
make setup

# 2. Start PostgreSQL 17 + pgvector
#    If host port 5432 is taken, set DOKTOK_DB_PORT=5433 in .env first.
make db

# 3. Run the backend (http://localhost:8000)
make run-backend
#    Health is public:    curl http://localhost:8000/health
#    The API is versioned under /api/v1 and needs a token (from .env):
#    curl -H "Authorization: Bearer dev-token-default" http://localhost:8000/api/v1/ingestion/jobs

# 4. In another terminal, run the UI (http://localhost:5173).
#    Use the make target so the dev proxy injects the bearer token for you.
make run-ui

# Run the full quality gate (Python + JS)
make check
```

## Running locally / starting after a reboot

DokTok NG runs as **manual foreground processes, each in its own terminal** - there is no daemon and
no aggregate start command. After a machine reboot (or a fresh shell), start things in this order:

```bash
# 1. Start Docker Desktop (the db container does NOT auto-start with it).
# 2. Start PostgreSQL 17 + pgvector
make db
# 3. Make sure Ollama is running (menu-bar app or `ollama serve`).

# 4. Terminal A - FastAPI backend (http://localhost:8000)
make run-backend
# 5. Terminal B - ingestion worker (auto-resumes its backlog)
make run-worker
# 6. Terminal C - UI dev server (http://localhost:5173)
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

## Authentication and multi-tenancy

DokTok NG is multi-tenant and the API is token-protected (ADR-0007, ADR-0008). HTTP routes are
versioned under `/api/v1` (`/health` is unversioned and public):

- Send `Authorization: Bearer <token>`; `/health` is public, `/api/v1/*` requires a token.
- Each token maps to a tenant (`DOKTOK_TENANT_TOKENS` is a JSON `{"<token>": "<tenant_id>"}` map).
  The default `.env` ships two: `dev-token-default` -> tenant `default` (used by the UI dev proxy)
  and `dev-token-developer` -> tenant `developer` (for your manual API testing). Example:
  `curl -H "Authorization: Bearer dev-token-developer" http://localhost:8000/api/v1/ingestion/jobs`
- All data is scoped to the caller's tenant: `tenant_id` on every table, and per-tenant filesystem
  folders. The backend binds loopback by default and fails closed if no tokens are configured.
- Static `.env` tokens now; DB-backed, hashed, revocable tokens later.

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

MIT — see [LICENSE](LICENSE).
