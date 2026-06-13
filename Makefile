.PHONY: help setup lint format typecheck test arch check \
        run-backend run-worker run-ui clean-tenant rag-eval enrich-eval ocr-paddle projection-engine db db-down \
        js-install js-typecheck js-lint js-test js \
        secrets sbom hooks

# Load local environment from .env (if present) and export it to every recipe.
# Command-line overrides (e.g. `make db DOKTOK_DB_PORT=5500`) still win.
-include .env
export

PY_SRC := contracts core apps/backend apps/worker apps/mcp storage modalities providers tools

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

run-backend: ## Run the FastAPI backend locally
	uv run uvicorn doktok_api.main:app --reload --port 8000

run-worker: ## Run the ingestion worker (watches each tenant's ingest folder)
	uv run doktok-worker

run-ui: ## Run the UI dev server (injects DOKTOK_DEV_TOKEN into proxied API calls)
	pnpm --filter @doktok/ui dev

clean-tenant: ## Wipe all DB rows + files for one tenant: make clean-tenant TENANT=developer
	@scripts/clean-tenant.sh $(TENANT)

rag-eval: ## Run the RAG evaluation harness against real Ollama (needs `make db` + Ollama)
	@scripts/rag-eval.sh

enrich-eval: ## Run the document-enrichment eval against real Ollama (needs `make db` + Ollama)
	@scripts/enrich-eval.sh

ocr-paddle: ## Install the PaddleOCR runtime (the DOKTOK_OCR_ENGINE=paddleocr extra)
	uv pip install paddleocr paddlepaddle pillow numpy

projection-engine: ## Install the embedding-projection runtime (PCA/UMAP/HDBSCAN for the Insights tab)
	uv pip install umap-learn scikit-learn hdbscan numpy

db: ## Start local Postgres + pgvector (docker compose)
	docker compose up -d

db-down: ## Stop local Postgres (keep volume)
	docker compose down

js-install: ## Install JS workspace dependencies
	pnpm install

js-typecheck: ## Typecheck JS/TS workspaces
	pnpm -r typecheck

js-lint: ## Lint JS workspaces
	pnpm -r lint

js-test: ## Test JS/TS workspaces (Vitest)
	pnpm -r test

js: js-typecheck js-lint js-test ## Run all JS/TS checks

secrets: ## Scan tracked files for secrets (detect-secrets)
	uvx detect-secrets scan --baseline .secrets.baseline

sbom: ## Generate a CycloneDX SBOM of runtime deps (sbom/python.cdx.json)
	@mkdir -p sbom
	uv export --no-dev --format requirements-txt 2>/dev/null | \
		uvx --from cyclonedx-bom cyclonedx-py requirements - -o sbom/python.cdx.json || \
		echo "SBOM generation skipped (cyclonedx-bom unavailable); see Makefile target 'sbom'."

hooks: ## Install git pre-commit hooks
	uvx pre-commit install

check: lint typecheck test arch js ## Run all checks (Python + JS)
