.PHONY: help setup lint format typecheck test arch check \
        run-backend run-worker run-ui preflight-backend preflight-worker clean-tenant create-tenant seed-dev rag-eval enrich-eval ocr-paddle ocr-rapid ocr-rapid-openvino projection-engine address-libpostal db db-down \
        js-install js-typecheck js-lint js-test js \
        secrets sbom hooks deploy-box drp-selftest backup verify-recovery restore-drill-dev

# Load local environment from .env (if present) and export it to every recipe.
# Command-line overrides (e.g. `make db DOKTOK_DB_PORT=5500`) still win.
-include .env
export

PY_SRC := contracts core apps/backend apps/worker apps/mcp storage modalities providers tools

# python-magic loads the native libmagic C library at runtime. On macOS with a
# non-default Homebrew prefix (e.g. ~/.local), the dynamic loader can't find it,
# so we add Homebrew's lib dir to the fallback search path. Harmless when unset.
BREW_PREFIX := $(shell brew --prefix 2>/dev/null)
ifneq ($(BREW_PREFIX),)
export DYLD_FALLBACK_LIBRARY_PATH := $(BREW_PREFIX)/lib:$(DYLD_FALLBACK_LIBRARY_PATH)
endif

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_.-]+:.*## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  %-16s %s\n", $$1, $$2}'

setup: ## Install Python (uv) and JS (pnpm) dependencies
	uv sync --all-packages
	pnpm install

lint: ## Ruff lint (Python)
	uv run ruff check .

format: ## Ruff format (Python)
	uv run ruff format .

typecheck: ## mypy type check (Python)
	uv run mypy $(PY_SRC)

test: ## Run Python tests
	uv run pytest

arch: ## Enforce hexagonal dependency direction (import-linter)
	uv run lint-imports

preflight-backend: ## Provision all local model-stack resources the backend may select (idempotent; DOKTOK_SKIP_PREFLIGHT=1 to skip)
	@bash scripts/preflight.sh backend

preflight-worker: ## Provision all local model-stack resources the worker may select (idempotent; DOKTOK_SKIP_PREFLIGHT=1 to skip)
	@bash scripts/preflight.sh worker

run-backend: preflight-backend ## Run the FastAPI backend locally (preflight provisions models first; DOKTOK_SKIP_PREFLIGHT=1 to skip; DOKTOK_BIND_HOST=0.0.0.0 exposes it to the LAN)
	uv run uvicorn doktok_api.main:app --reload --port 8000 --host $${DOKTOK_BIND_HOST:-127.0.0.1}

run-worker: preflight-worker ## Run the ingestion worker (preflight provisions models first; watches each tenant's ingest folder)
	uv run doktok-worker

run-ui: ## Run the UI dev server (injects DOKTOK_DEV_TOKEN into proxied API calls)
	pnpm --filter @doktok/ui dev

clean-tenant: ## Wipe all DB rows + files for one tenant: make clean-tenant TENANT=developer
	@scripts/clean-tenant.sh $(TENANT)

create-tenant: ## Provision a usable tenant (row + folders + bootstrap token): make create-tenant NAME="Staging" [ARGS=--admin-email a@b.com --admin-password ...]
	@scripts/create-tenant.sh "$(NAME)" $(ARGS)

seed-dev: ## Seed a 'dev' tenant + admin/editor/viewer users for UI login (local/dev only; ARGS=--reset)
	@scripts/seed-dev.sh $(ARGS)

rag-eval: ## Run the RAG evaluation harness against real Ollama (needs `make db` + Ollama)
	@scripts/rag-eval.sh

enrich-eval: ## Run the document-enrichment eval against real Ollama (needs `make db` + Ollama)
	@scripts/enrich-eval.sh

ner-bench: ## Benchmark NER: current LLM vs local GLiNER vs NuNER (needs a provider; `make ner-models` for gliner/nuner)
	@scripts/ner-bench.sh

kg-bench: ## Benchmark KG relations: current LLM vs local GLiNER-Relex (needs a provider; `make ner-models`)
	@scripts/kg-bench.sh

ner-models: ## Install the local GLiNER/NuNER/GLiNER-Relex runtime (doktok-provider-gliner[engine])
	uv pip install gliner rapidfuzz

reranker-models: ## Install the local Qwen3-Reranker runtime (doktok-provider-reranker[engine])
	uv pip install "torch>=2.2" "transformers>=4.51"

ocr-paddle: ## Install the PaddleOCR runtime (paddleocr extra; not in lockfile - re-run after any `uv sync`)
	uv pip install paddleocr paddlepaddle pillow numpy

ocr-rapid: ## Install the RapidOCR runtime (rapidocr extra, ~6x faster on weak CPUs; re-run after any `uv sync`)
	uv pip install rapidocr-onnxruntime pillow numpy

ocr-rapid-openvino: ## Install RapidOCR + the OpenVINO backend (Intel; ~20x vs Paddle on N95). Needs openvino<2025.
	uv pip install rapidocr-openvino "openvino<2025" pillow numpy

projection-engine: ## Install the embedding-projection runtime (PCA/UMAP/HDBSCAN for the Insights tab)
	uv pip install umap-learn scikit-learn hdbscan numpy

address-libpostal: ## Install the libpostal address-parsing runtime (needs the C lib: `brew install libpostal`; not in lockfile - re-run after any `uv sync`)
	uv pip install postal

db: ## Start local Postgres + pgvector and Gotenberg (docker compose)
	docker compose up -d

db-down: ## Stop local Postgres (keep volume)
	docker compose down

# Backup/restore on the dev box, the SAME scripts as prod (#745): the db container gets the prod
# pgbackrest wiring via docker-compose.dev.yml, the files leg runs in the backup-runner service.
DEV_COMPOSE_FILES=docker-compose.yml,docker-compose.dev.yml

db-dev-backup-image: ## Build the dev db image (pg17 + pgvector + pgBackRest) for backups
	docker compose -f docker-compose.yml -f docker-compose.dev.yml build db

dev-backup: ## Back up files_root + Postgres on the dev box (compose mode, same as prod)
	DOKTOK_DEPLOY_MODE=compose DOKTOK_COMPOSE_FILES=$(DEV_COMPOSE_FILES) DOKTOK_COMPOSE_ENV_FILE=.env \
		./deploy/backup.sh $(TYPE)

dev-backup-pg-logical: ## Logical pg_dump safety-net into ./backups/pg/logical
	./deploy/backup-pg-logical.sh

dev-pg-wal-freshness: ## Stamp the pg sentinel with the WAL recovery point (prod runs this every minute via systemd; schedule it in dev via cron for continuous DRP freshness)
	DOKTOK_DEPLOY_MODE=compose DOKTOK_COMPOSE_FILES=$(DEV_COMPOSE_FILES) DOKTOK_COMPOSE_ENV_FILE=.env \
		./deploy/pg-wal-freshness.sh

dev-azure-sync: ## Push the local backup repo to Azure Blob (offsite leg; needs DOKTOK_AZURE_* in .env)
	@export DOKTOK_AZURE_ACCOUNT="$$(grep '^DOKTOK_AZURE_ACCOUNT=' .env | cut -d= -f2-)" \
		DOKTOK_AZURE_CONTAINER="$$(grep '^DOKTOK_AZURE_CONTAINER=' .env | cut -d= -f2-)" \
		DOKTOK_AZURE_SAS="$$(grep '^DOKTOK_AZURE_SAS=' .env | cut -d= -f2-)" \
		DOKTOK_GFS_BASE_CLASS="$$(grep '^DOKTOK_GFS_BASE_CLASS=' .env | cut -d= -f2-)" \
		DOKTOK_COMPOSE_FILES=$(DEV_COMPOSE_FILES) DOKTOK_COMPOSE_ENV_FILE=.env; \
	./deploy/azure-sync.sh

dev-azure-fetch: ## Fetch an offsite backup set from Azure into ./backups.azure-restore (TS=<timestamp> for a specific set)
	@export DOKTOK_AZURE_ACCOUNT="$$(grep '^DOKTOK_AZURE_ACCOUNT=' .env | cut -d= -f2-)" \
		DOKTOK_AZURE_CONTAINER="$$(grep '^DOKTOK_AZURE_CONTAINER=' .env | cut -d= -f2-)" \
		DOKTOK_AZURE_SAS="$$(grep '^DOKTOK_AZURE_SAS=' .env | cut -d= -f2-)" \
		DOKTOK_COMPOSE_FILES=$(DEV_COMPOSE_FILES) DOKTOK_COMPOSE_ENV_FILE=.env; \
	./deploy/azure-fetch.sh ./backups.azure-restore $(TS)

dev-restore: ## Restore Postgres + files_root from the local repo (DESTRUCTIVE; usage: make dev-restore FILES_TARGET=./storage/files [PITR="YYYY-MM-DD HH:MM:SS+00"])
	DOKTOK_DEPLOY_MODE=compose DOKTOK_COMPOSE_FILES=$(DEV_COMPOSE_FILES) DOKTOK_COMPOSE_ENV_FILE=.env \
		./deploy/restore.sh $(FILES_TARGET) $(if $(PITR),"$(PITR)",)

js-install: ## Install JS workspace dependencies
	pnpm install

js-typecheck: ## Typecheck JS/TS workspaces
	pnpm -r typecheck
	@[ -d apps/mobile/node_modules ] && (cd apps/mobile && pnpm typecheck) \
		|| echo "apps/mobile not installed (make mobile-install) - skipping"

js-lint: ## Lint JS workspaces
	pnpm -r lint

js-test: ## Test JS/TS workspaces (Vitest)
	pnpm -r test

js: js-typecheck js-lint js-test ## Run all JS/TS checks

mobile-install: ## Install apps/mobile deps (standalone - Expo/RN conflicts with the UI's React 18, so it lives OUTSIDE the pnpm workspace)
	cd apps/mobile && pnpm install --ignore-workspace

ANDROID_HOME ?= $(HOME)/Library/Android/sdk
EMULATOR ?= doktok

mobile-emulator-start: ## Start the Android emulator detached (EMULATOR=name, default doktok)
	@$(ANDROID_HOME)/emulator/emulator -avd $(EMULATOR) -gpu auto -no-snapshot-save -no-metrics \
		>/tmp/doktok-emulator.log 2>&1 & \
		echo "emulator '$(EMULATOR)' starting in background (log: /tmp/doktok-emulator.log); boot state: make mobile-emulator-status"

mobile-emulator-status: ## Wait for the emulator and print its boot state (1 = booted)
	@$(ANDROID_HOME)/platform-tools/adb wait-for-device
	@$(ANDROID_HOME)/platform-tools/adb shell getprop sys.boot_completed

mobile-emulator-stop: ## Stop the Android emulator
	@$(ANDROID_HOME)/platform-tools/adb emu kill || true

mobile-run: ## Build + install the app on the running emulator (needs JDK 17 + local.properties sdk.dir)
	cd apps/mobile && pnpm exec expo run:android

mobile-deploy: ## Redeploy on the USB phone: auto-detects it, ensures adb reverse tunnels (8000 backend + 8081 Metro), then builds + installs
	@adb="$(ANDROID_HOME)/platform-tools/adb"; \
	serial="$$($$adb devices | grep -v 'List of devices' | grep -v emulator | cut -f1 | head -1)"; \
	if [ -z "$$serial" ]; then \
		echo "no USB phone found - connect the cable and enable USB debugging on the phone"; exit 1; \
	fi; \
	echo "phone: $$serial"; \
	$$adb -s "$$serial" reverse --list 2>/dev/null | grep -q "tcp:8000" \
		|| $$adb -s "$$serial" reverse tcp:8000 tcp:8000; \
	$$adb -s "$$serial" reverse --list 2>/dev/null | grep -q "tcp:8081" \
		|| $$adb -s "$$serial" reverse tcp:8081 tcp:8081; \
	echo "tunnels OK (phone localhost:8000 -> backend, :8081 -> Metro)"; \
	cd apps/mobile/android && ANDROID_SERIAL="$$serial" ./gradlew installDebug

mobile-start: ## Start the Metro dev server in DEV-CLIENT mode (this project has native modules; Expo Go can't run it)
	cd apps/mobile && pnpm exec expo start --dev-client

mobile-start-clear: ## Start Metro (dev-client) with a cleared cache (after dependency/resolution issues)
	cd apps/mobile && pnpm exec expo start --dev-client --clear

secrets: ## Scan tracked files for secrets (detect-secrets)
	uvx detect-secrets scan --baseline .secrets.baseline

sbom: ## Generate a CycloneDX SBOM of runtime deps (sbom/python.cdx.json)
	@mkdir -p sbom
	uv export --no-dev --format requirements-txt 2>/dev/null | \
		uvx --from cyclonedx-bom cyclonedx-py requirements - -o sbom/python.cdx.json || \
		echo "SBOM generation skipped (cyclonedx-bom unavailable); see Makefile target 'sbom'."

hooks: ## Install git pre-commit hooks
	uvx pre-commit install

deploy-box: ## Deploy the working tree to the compose box: rsync + rebuild (live progress) + up -d. Override DOKTOK_BOX_HOST/KEY/DIR/SERVICES; DOKTOK_BOX_NO_BUILD=1 to skip the rebuild.
	@deploy/deploy-to-box.sh

drp-selftest: ## No-risk DRP self-test: Postgres PITR proof + portable export/restore round-trip (throwaway containers; needs Docker)
	@deploy/drp-selftest.sh

verify-recovery: ## No-risk dev recovery check: round-trip the LIVE dev DB + files into a throwaway Postgres and assert documents + enriched/extracted rows survive. Run after ingesting. (needs `make db`)
	@deploy/verify-recovery-dev.sh

restore-drill-dev: ## DESTRUCTIVE dev disaster drill on the real engine: baseline -> backup -> WIPE -> restore (PITR) -> verify counts/hashes/API. Asks to confirm (FORCE=1 to skip). Stop run-backend/run-worker first.
	DOKTOK_DEPLOY_MODE=compose DOKTOK_COMPOSE_FILES=$(DEV_COMPOSE_FILES) DOKTOK_COMPOSE_ENV_FILE=.env \
		deploy/restore-drill-dev.sh

dev-drill: ## NO-RISK dev restore drill (same script as prod's weekly timer): restores the latest files snapshot + pg backup into throwaway targets and verifies counts/hashes vs live; writes the DRP drill sentinel.
	DOKTOK_DEPLOY_MODE=compose DOKTOK_COMPOSE_FILES=$(DEV_COMPOSE_FILES) DOKTOK_COMPOSE_ENV_FILE=.env \
		./deploy/restore-drill.sh

TYPE ?= full
backup: ## Run a backup now -> populates the DRP (sentinels + history). Honors DOKTOK_DEPLOY_MODE; TYPE=full|diff|incr (default full). The on-demand, no-systemd path for dev.
	@deploy/backup.sh $(TYPE)

check: lint typecheck test arch js ## Run all checks (Python + JS)
