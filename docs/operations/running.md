# Running locally and restarting after a reboot

DokTok NG runs as a set of **manual foreground processes**, each in its own terminal. There is no
daemon, no LaunchAgent, and no aggregate "start everything" target: you start the database, then the
backend, worker, and UI by hand. This keeps each process's logs in front of you and matches the
local-first, single-developer model.

After a machine reboot (or a fresh shell), nothing in this list auto-starts on its own except what
you have separately configured to launch at login (Docker Desktop, the Ollama menu-bar app). The rest
you start in the order below.

## Prerequisites

These must be installed once (see the README Quickstart for details):

- [`uv`](https://docs.astral.sh/uv/) (Python workspace) and [`pnpm`](https://pnpm.io/) (JS workspace)
- Docker (Docker Desktop on macOS) for the Postgres and Gotenberg containers
- `libmagic` (macOS: `brew install libmagic`)
- [Ollama](https://ollama.com/) with the default models pulled (see `.env` / ADR-0003)

A populated `.env` (copied from `.env.example`). `make` and the app load it automatically. `.env` is
gitignored, so it lives only on this machine.

## Startup order after a reboot

Run these in order. Steps 4-6 each stay in the foreground in their **own terminal**.

```bash
# 1. Start Docker Desktop (the app). The containers do NOT auto-start with it.

# 2. Start the Docker services (PostgreSQL 17 + pgvector, and Gotenberg for office conversion).
#    `make db` runs `docker compose up -d`, which brings up both the doktok-db and
#    doktok-gotenberg containers.
make db

# 3. Make sure Ollama is running (the Ollama menu-bar app, or `ollama serve`).
#    Confirm the model server is up:
curl -s http://localhost:11434/api/tags >/dev/null && echo "ollama up"

# 4. Terminal A - FastAPI backend (http://localhost:8000)
make run-backend

# 5. Terminal B - ingestion worker (auto-resumes; see below)
make run-worker

# 6. Terminal C - UI dev server (http://localhost:5173)
make run-ui
```

Quick health check once the backend is up:

```bash
curl http://localhost:8000/health
```

### What each command does

| Command | Process | Notes |
|---|---|---|
| `make db` | `docker compose up -d` (containers `doktok-db`, `doktok-gotenberg`) | Detached. Postgres 17 + pgvector on `DOKTOK_DB_PORT` (default `5432`); Gotenberg (office -> PDF) on `DOKTOK_GOTENBERG_PORT` (default `3000`). |
| `make run-backend` | `uvicorn doktok_api.main:app --reload --port 8000` | Foreground. Serves `/api/v1` (token-protected) and public `/health`. |
| `make run-worker` | `uv run doktok-worker` | Foreground. Watches each tenant's `ingest/` folder and runs the pipeline. |
| `make run-ui` | `pnpm --filter @doktok/ui dev` | Foreground. Vite dev server; the dev proxy injects the bearer token. |

Stop any foreground process with `Ctrl+C`. Stop the database with `make db-down` (this keeps the
volume; see below).

## What persists vs. what you restart

Across a reboot, **state persists; processes do not.** You restart the four processes (db container,
backend, worker, UI), but you do not lose data.

### Persists across a reboot

- **All Postgres data** lives in the named Docker volume `doktok-pgdata` (see `docker-compose.yml`).
  That includes documents, chunks and embeddings, extracted entities, chat threads
  (`chat_threads` / `chat_messages`), categories, the audit log, and application settings in
  `app_settings` (the runtime AI model selection `ai_settings` and OCR settings `ocr_settings`).
  `make db-down` stops the container **but keeps this volume**, so data survives both reboots and a
  normal stop. (Running `docker compose down -v` would delete the volume - do not do that unless you
  intend to wipe the database.)
- **The local file tree** under `storage/files/{tenant}/...`: originals and OCR/normalized output in
  `docs.active/`, plus anything still sitting in `ingest/`, `in.process/`, `docs.failed/`,
  `duplicates/`, and `quarantine/`. This tree is gitignored and lives only on this machine.
- **Ollama models** on disk, and the **code / git working tree**.

### Does not auto-start (you restart it)

- **Docker Desktop** - unless you have set it to launch at login.
- **The `doktok-db` container** - `docker-compose.yml` sets **no `restart:` policy**, so the
  container does not come back when Docker starts. You run `make db`.
- **The `doktok-gotenberg` container** - converts office documents (`.docx`/`.xlsx`/`.pptx`) to PDF
  on ingest. It has `restart: unless-stopped`, so once started it returns with the Docker daemon, but
  `make db` starts it the first time. If it is unreachable, office ingestion fails with `needs_ocr`.
- **Ollama** - unless the menu-bar app is a login item.
- **The backend, worker, and UI** - always started by hand with the Make targets above.

## The worker auto-resumes where it left off

When you start `make run-worker` after a reboot (or any restart), it picks ingestion back up without
manual intervention. Two recovery mechanisms run before it processes the live queue:

1. **Stale-job recovery.** A job that a previous worker was killed in the middle of (for example,
   during OCR) is left in a non-terminal state with its source file stranded under `in.process/`,
   where it would otherwise be invisible and never become a document. On startup (and periodically),
   the worker moves each such stranded file back into that tenant's `ingest/` folder and drops the
   stale job, so the normal scan reprocesses it cleanly. See
   `recover_stale_jobs` in `core/doktok_core/ingestion/pipeline.py`
   (driven by `IngestionWorker.recover_stale` in `apps/worker/doktok_worker/worker.py`).
2. **Ingest-folder rescan.** Any file still sitting in a tenant's `ingest/` folder (anything that had
   not finished when the worker stopped) is detected once it is stable and ingested. So a backlog
   left in `ingest/` is simply processed on the next run.

A parallel reconciliation stream similarly re-queues per-document features that a prior worker left
mid-flight, so partially processed documents are completed rather than stuck.

Practically: after a reboot you start the worker and ingestion continues from where it stopped. You
do not need to re-drop files that were already in `ingest/`.

## Default models

The worker and backend call a local Ollama server. The defaults (from `.env`; see ADR-0003 for chat
and ADR-0010 for OCR) are:

```env
DOKTOK_DEFAULT_MODEL=qwen3.6:35b-a3b          # RAG chat / reranker
DOKTOK_EMBEDDING_MODEL=qwen3-embedding:0.6b    # 1024-dim embeddings
DOKTOK_EMBEDDING_NUM_CTX=1024                  # cap embedding context (chunks ~300 tok); frees KV-cache
DOKTOK_ENRICH_MODEL=qwen3:14b                  # enrichment + OCR-quality judge + JSON repair (one model)
DOKTOK_OCR_ENGINE=paddleocr                    # PP-OCRv5, CPU-only (no Ollama call for default OCR)
```

Make sure these models are pulled in Ollama before ingesting. PaddleOCR runs locally and needs its
extra installed on the worker host (`make ocr-paddle`); the default OCR engine does not call Ollama.
For throughput tuning see [performance-and-ollama.md](performance-and-ollama.md).

## Troubleshooting startup

| Symptom | Likely cause | Fix |
|---|---|---|
| `make db` fails / port already in use | Another local Postgres on `DOKTOK_DB_PORT` | Set `DOKTOK_DB_PORT=5433` (or another free port) in `.env`, then `make db`. |
| Backend or worker can't reach the database | `doktok-db` container not running | Run `make db`; confirm with `docker ps` (container `doktok-db`). |
| Worker or chat errors mentioning the model server | Ollama not running, or model not pulled | Start Ollama; `ollama list` to confirm the models in `.env` are present. |
| OCR ingestion fails after a dependency change | PaddleOCR extra pruned by `uv sync` | Re-run `make ocr-paddle` on the worker host. |
| Office (`.docx`/`.xlsx`/`.pptx`) ingestion fails with `needs_ocr` | Gotenberg container not running or unreachable | Run `make db` (starts `doktok-gotenberg`); confirm with `docker ps`. Check `DOKTOK_GOTENBERG_URL` / `DOKTOK_GOTENBERG_PORT`. |
| UI loads but API calls are unauthorized | UI started without the dev proxy | Start the UI via `make run-ui` so the proxy injects the bearer token. |

## Deploying to a small server (hybrid)

This page covers the local single-machine workflow. To deploy DokTok NG to small hardware (a TRIGKEY
N95: 4 cores, 8 GB, no GPU) for staging and limited production — where the local LLMs do not fit, so
enrichment and chat run on OpenAI while OCR and embeddings stay local — see the operator guide
[deployment-trigkey-n95.md](deployment-trigkey-n95.md) and its founding decision
[ADR-0020](../adr/ADR-0020-hybrid-deployment-topology.md). That guide also flags which deployment
pieces are shipped today versus planned in the M11 epic.

## Future option

Nothing here requires a single start command. If the per-terminal flow ever becomes tedious, a thin
wrapper (a script or a `make up`-style target that launches the processes) could be added later, but
the project deliberately keeps each process in its own terminal for now so logs stay visible and any
one stream can be restarted independently.
