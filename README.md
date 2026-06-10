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

**M1 (Folder ingestion).** Building on the M0 skeleton, DokTok NG now ingests files dropped into a
watched folder: a worker detects stable files, atomically moves them into the document lifecycle,
computes SHA-256, detects MIME by content (libmagic), validates against the security policy
(allowlist + size limit, quarantine for dangerous types, dedup by hash), and records database-backed
ingestion jobs. The backend exposes the ingestion job API and the UI lists jobs. Extraction (turning
files into active documents) arrives in M2. See the [milestone roadmap](docs/milestones/M0-M10.md).

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

```bash
# 1. Install dependencies (Python uv workspace + JS pnpm workspace)
make setup

# 2. Start PostgreSQL 17 + pgvector
#    If host port 5432 is taken, set DOKTOK_DB_PORT (e.g. 5433) first.
make db

# 3. Run the backend (http://localhost:8000)
make run-backend
#    Health check: curl http://localhost:8000/health

# 4. In another terminal, run the UI (http://localhost:5173)
pnpm --filter @doktok/ui dev

# Run the full quality gate (Python + JS)
make check
```

Copy `.env.example` to `.env` to override defaults (models, database URL, limits).

### Ingesting documents (M1)

```bash
# Start the database, then run the worker (it creates the lifecycle folders and runs migrations)
make db
make run-worker

# Drop a file into the ingest folder
cp some-document.pdf storage/files/ingest/
```

The worker waits until the file is stable, moves it into `storage/files/in.process/{job_id}/source`,
hashes it, detects its MIME type, and records an ingestion job. Watch progress via the API
(`GET /api/ingestion/jobs`) or the **Ingestion** tab in the UI. Unsupported types are rejected to
`storage/files/docs.failed/`; dangerous types are isolated to `storage/files/quarantine/`; duplicate
content (same SHA-256) is flagged. Extraction into active documents arrives in M2.

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
