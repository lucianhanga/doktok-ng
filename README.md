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

Pre-M0. This repository currently contains the architecture, ADRs, milestone roadmap, and the tracked
backlog. Application code lands milestone by milestone, starting with **M0 (Skeleton)**.

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

A working quickstart (`docker compose up`, backend `/health`, UI shell) ships with **M0**. Until then,
this repository is documentation and roadmap only.

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
