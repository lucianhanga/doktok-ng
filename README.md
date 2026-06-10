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

**M4 (Vector + full-text search).** On top of M3, every activated document is **chunked, embedded
(Ollama `mxbai-embed-large` → pgvector) and full-text indexed** before it goes active. **Hybrid search**
(semantic vector + Postgres FTS, fused with Reciprocal Rank Fusion) is exposed at `GET /api/v1/search`
and a **Search** tab in the UI — tenant-scoped and token-protected. Earlier milestones below.

**M3 (OCR extraction).** Files dropped into a tenant's ingest folder are detected, validated, and
**extracted into active documents**. Born-digital `.txt`/`.md`/PDF use direct/PyMuPDF extraction;
**scanned PDFs and images are OCR'd via a local Ollama vision model** (`DOKTOK_OCR_MODEL`, default
`glm-ocr:latest`) and a derived `normalized/searchable.pdf` (images + invisible OCR text layer) becomes
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
DOKTOK_DEFAULT_MODEL=qwen3.6:35b-a3b
DOKTOK_EMBEDDING_MODEL=mxbai-embed-large:latest
DOKTOK_OLLAMA_BASE_URL=http://localhost:11434
```

Both model names are configurable via environment variables. See ADR-0003 for the rationale and a
documented embedding alternative (`bge-m3:latest`).

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
