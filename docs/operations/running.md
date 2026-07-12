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
| `make run-backend` | `uvicorn doktok_api.main:app --reload --port 8000` | Foreground. Runs the backend model-stack preflight first (see below), then serves `/api/v1` (token-protected) and public `/health`. |
| `make run-worker` | `uv run doktok-worker` | Foreground. Runs the worker model-stack preflight first (see below), then watches each tenant's `ingest/` folder and runs the pipeline. |
| `make run-ui` | `pnpm --filter @doktok/ui dev` | Foreground. Vite dev server; the dev proxy injects the bearer token. |

Stop any foreground process with `Ctrl+C`. Stop the database with `make db-down` (this keeps the
volume; see below).

## Model-stack preflight

`make run-backend` and `make run-worker` each run a **preflight** step first (the Make targets
`preflight-backend` / `preflight-worker`, which call `scripts/preflight.sh`). The preflight
provisions every *local* model-stack resource that service could select **before** the service
starts, so a freshly synced checkout is ready to ingest and answer without a first-request stall.

- **Per-service scope.** Each service provisions only the runtimes and models it actually uses.
  The **worker** installs the OCR (Paddle + Rapid), GLiNER NER/Relex, and projection-engine
  runtimes, pulls the local chat/enrich model, the embedding model, and the `glm-ocr` vision model,
  and prefetches the GLiNER NER + Relex weights. The **backend** installs the Qwen3-Reranker
  runtime, pulls the chat/RAG model and the embedding model, and prefetches both Qwen3-Reranker
  weight sets. The exact list is derived from `MODEL_CATALOG` (`core/doktok_core/settings/catalog.py`)
  plus the configured defaults in `core/doktok_core/config.py`, so it stays correct as those change.
  Remote OpenAI options are egress-gated and are never pulled.
- **Idempotent.** `uv pip install` and `ollama pull` both skip work already present, so a warm run
  is quick with no re-downloads; a cold run does the one-time installs and pulls.
- **Tolerant of a missing Ollama / offline HF.** If the `ollama` CLI is absent or the daemon is
  unreachable, or a Hugging Face prefetch fails, the preflight prints a yellow warning and
  **continues** - those artifacts download on first use later. Only a genuine `pip` install failure
  stops the run target.
- **Escape hatch.** Set `DOKTOK_SKIP_PREFLIGHT=1` to skip the preflight entirely (for example when
  you are offline and manage the models yourself): `DOKTOK_SKIP_PREFLIGHT=1 make run-worker`.

You can also run a provisioning pass on its own without starting the service, e.g. `make
preflight-worker` or `make preflight-backend`.

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
# Enrichment + OCR-quality judge follow the Data Pipeline model selected in the UI (no hardcoded model)
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
| OCR ingestion fails with `ModuleNotFoundError` (`paddleocr`/`rapidocr`) after a dependency change | The OCR runtime extra was pruned by `uv sync` — these extras are intentionally **not** in the lockfile, so any `uv sync`/`uv run --frozen` removes them | Normally the model-stack preflight (above) restores these automatically the next time you `make run-worker` / `make run-backend`, since it re-runs `make ocr-paddle`, `make ocr-rapid`, `make ner-models`, `make projection-engine` (worker) and `make reranker-models` (backend) idempotently. If you skipped it with `DOKTOK_SKIP_PREFLIGHT=1`, re-run the matching target on the worker host by hand (`make ocr-paddle`, `make ocr-rapid`, or `make ocr-rapid-openvino`), then **restart the worker** (an engine/runtime change applies only on restart). The engine the worker uses is the stored `ocr_settings.engine`, falling back to `DOKTOK_OCR_ENGINE` (default `paddleocr`) when unset — the value shown in Settings may be a device *recommendation*, not the applied engine. |
| Office (`.docx`/`.xlsx`/`.pptx`) ingestion fails with `needs_ocr` | Gotenberg container not running or unreachable | Run `make db` (starts `doktok-gotenberg`); confirm with `docker ps`. Check `DOKTOK_GOTENBERG_URL` / `DOKTOK_GOTENBERG_PORT`. |
| UI loads but API calls are unauthorized | UI started without the dev proxy | Start the UI via `make run-ui` so the proxy injects the bearer token. |

## Tenant and user management in your dev environment

The tenant/user/login stack (EPIC #523,
[ADR-0024](../adr/ADR-0024-tenant-user-management-and-rbac.md)) needs no setup to try locally:
pull, then start the stack as above (`make db`, `make run-backend`, `make run-worker`,
`make run-ui`). The registry migrations (`0043`-`0049`: tenants, users, api_tokens, credentials,
roles, preferences, invitations) auto-apply the first time the backend or worker touches the
database - there is no separate migration step.

### Token-free mode (the default)

With no signing secret configured, `/auth/login` answers 503, the UI shows **no login screen**, and
everything works exactly as before:

- **The Admin tab works immediately.** The UI dev proxy injects `DOKTOK_DEV_TOKEN`
  (`dev-token-default` from `.env`), and a tenant-scoped token with no user identity resolves to
  **admin**, so the Admin tab (members, roles, invitations, API tokens) is fully usable with no
  login. One-time secrets (invite tokens, issued API tokens) are shown exactly once - copy them
  when displayed.
- **Preference sync is automatic.** UI preferences (table layouts, thumbnail size, chat mode, ...)
  mirror transparently to the server per identity (`/api/v1/preferences`); with the dev token that
  is one per-tenant bucket. Nothing to configure; offline it degrades to localStorage.

### Enabling login and seeing RBAC in action

1. Set a signing secret in `.env` and restart the backend (mint a dedicated one; do not lean on
   the `DOKTOK_SECRETS_KEY` fallback - the backend warns about it at startup):

   ```env
   DOKTOK_AUTH_JWT_SECRET=<paste output of: openssl rand -base64 48>  # pragma: allowlist secret
   ```

2. Seed a `dev` tenant with one user per role (idempotent; refuses outside a local/dev environment
   with a loopback database; see [ADR-0024](../adr/ADR-0024-tenant-user-management-and-rbac.md)):

   ```bash
   make seed-dev
   ```

   This creates `dev-admin@doktok.local` (admin), `dev-editor@doktok.local` (editor), and
   `dev-viewer@doktok.local` (viewer). Passwords come from `DOKTOK_DEV_SEED_PASSWORD` in `.env`
   (min 12 chars, reproducible logins) or are generated and printed **once** - save them.
   `make seed-dev ARGS=--reset` rotates the passwords.

3. `make run-ui`, open http://localhost:5173 - the SPA sees login enabled (`GET /auth/config`) and
   shows the login screen. Sign in with tenant `dev` and one of the emails above. A signed-in bar
   shows your identity and role. Try the viewer (read-only: content writes are 403, no Admin tab
   access), the editor (can ingest/edit, still no admin), then the admin. Log out (or close the
   tab - the session is per-tab) to switch users. The dev proxy still injects the dev token, but
   **only for requests without an Authorization header**, so your logged-in session is never
   silently overridden.

Login attempts are throttled per account (default 5/min) and per IP (default 20/min) with
429 + `Retry-After`, and every attempt lands in the Activity tab (`auth.login.succeeded` /
`auth.login.failed`).

### The same flows over curl

Create a member with a password (admin API; the dev token acts as admin). Prompt for the password
so no literal ends up in your shell history:

```bash
read -r -s -p "New member password (min 12 chars): " DOKTOK_PW && echo

# 1. Create a member (role: viewer | editor | admin)
curl -s -X POST http://localhost:8000/api/v1/admin/users \
  -H "Authorization: Bearer <dev-token>" -H "Content-Type: application/json" \
  -d "{\"email\": \"me@example.com\", \"display_name\": \"Me\", \"role\": \"editor\", \"password\": \"$DOKTOK_PW\"}"

# 2. Log in -> a short-lived session JWT (default TTL 3600 s)
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"tenant_id\": \"default\", \"email\": \"me@example.com\", \"password\": \"$DOKTOK_PW\"}" \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])')

# 3. The JWT works everywhere a token does; /auth/me shows who you are
curl -s http://localhost:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
```

**Invite flow.** An admin invites an email; the invitee sets their own password via the public
accept endpoint (the one-time token is the credential; default validity 168 h,
`DOKTOK_AUTH_INVITE_TTL_HOURS`):

```bash
# Admin: invite (the response contains "token" - shown ONCE)
curl -s -X POST http://localhost:8000/api/v1/admin/invitations \
  -H "Authorization: Bearer <dev-token>" -H "Content-Type: application/json" \
  -d '{"email": "friend@example.com", "role": "viewer"}'

# Invitee: accept (no auth header - the invite token is the credential)
read -r -s -p "Choose a password (min 12 chars): " DOKTOK_PW && echo
curl -s -X POST http://localhost:8000/api/v1/auth/accept-invite \
  -H "Content-Type: application/json" \
  -d "{\"token\": \"<the-one-time-invite-token>\", \"password\": \"$DOKTOK_PW\"}"
```

(Replace `<dev-token>` with your `DOKTOK_DEV_TOKEN` value from `.env`.)

Deactivating a member (`POST /api/v1/admin/users/{id}/deactivate`, or the Admin tab) blocks their
session JWTs and API tokens on their next request. Role changes, invites, token issue/revoke,
logins, and deactivations are all in the Activity tab, attributed to the acting identity.

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
