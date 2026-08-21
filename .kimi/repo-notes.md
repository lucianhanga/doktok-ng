# DokTok NG — internal repo notes (agent working memory)

Compiled by exploring the whole repo (13 parallel area reports). Purpose: durable orientation so I can
work on this codebase without re-deriving structure. Trust code over docs where they disagree (docs drift
noted below). Last compiled: 2026-07-17.

## 1. Big picture

- **What**: local-first, AI-enabled document-intelligence system ("Paperless-ngx + local RAG + MCP +
  entity search"). Ingests files from per-tenant folders → OCR/extract → chunk+embed+index → hybrid
  search → RAG chat with `[n]` citations and refusal; read-only MCP server; knowledge graph (KAG);
  DRP backup/restore. Multi-tenant, token-protected, RBAC (viewer<editor<admin).
- **Status**: ~M13+ shipped (roadmap file `docs/milestones/M0-M10.md` is stale; trust
  `docs/operations/deployment-trigkey-n95.md`). M8 MCP partially built (3 of 7 planned tools), M9/M10 open.
- **Principles**: local-first/no egress by default; PG 17 + pgvector is the single storage spine; hybrid
  retrieval never vector-only; treat all files as untrusted; modular monolith, ports & adapters; every
  milestone ships runnable; maintained by one dev + coding agents.
- **License**: PolyForm Noncommercial 1.0.0 (MIT grandfathered pre-2026-06-26).

## 2. Repo mechanics

- **Python**: uv workspace, 18 members, Python 3.12, root `pyproject.toml` holds ALL shared tool config
  (ruff line-length 100, rules `E,F,I,UP,B,W,C4,SIM`; mypy **strict** with `ignore_errors` only for vendored
  `doktok_provider_gliner.refiners.*`/`kg_refiners.*`; pytest `-q --import-mode=importlib` with 12 pinned
  `testpaths`; import-linter contracts).
- **JS**: pnpm workspace, single member `apps/ui` (`@doktok/ui`), pnpm 11.9.0.
- **Quality gate**: `make check` = lint + typecheck + test + arch + js. `make arch` = `uv run lint-imports`.
- **Layering (import-linter enforced)**: `doktok_contracts` + `doktok_core` may NOT import any
  adapter/app package; `doktok_contracts` may not import `doktok_core`. Adapters→core is allowed and used.
  Known violations NOT caught: storage-postgres and providers/ollama+openai import `doktok_core` without
  declaring it in their pyproject (workspace hides it); contracts uses pydantic without declaring it.
- **Key Make targets**: `setup`, `db`/`db-down` (base + dev compose files, so the db always keeps the
  pgBackRest backup wiring — a plain `docker compose up -d` recreates doktok-db from the stock
  pgvector image and silently breaks the pg backup leg), `run-backend`/
  `run-worker` (gated on preflight which provisions models; `DOKTOK_SKIP_PREFLIGHT=1` skips), `run-ui`,
  `test`, `js`, `secrets`, `sbom`; model extras (`ocr-paddle`, `ocr-rapid`, `ocr-rapid-openvino`,
  `ner-models`, `reranker-models`, `projection-engine`) are NOT in uv.lock — `uv sync` prunes them, re-run
  target + restart worker; tenant/eval (`create-tenant`, `clean-tenant`, `seed-dev`, `rag-eval`,
  `enrich-eval`, `ner-bench`, `kg-bench`); ops (`deploy-box`, `backup TYPE=`, `drp-selftest`,
  `verify-recovery`, `restore-drill-dev`).
- **CI** (`.github/workflows/`): `ci.yml` (ruff+mypy+pytest w/ pg service+import-linter; pnpm
  typecheck/lint/test; detect-secrets + informational pip-audit; docs-only changes skipped). NOTE: CI mypy
  scope is narrower than local (`providers`,`tools` excluded in CI). `release.yml` builds/pushes
  backend/worker/ui images to GHCR (Trivy informational, SBOM artifact). `deploy.yml` manual-only, env-gated
  SSH deploy w/ pre-deploy backup + smoke test. `backup-watchdog.yml` cron scraping `/metrics` backup gauges
  (fails OPEN if secrets unset).
- **Tests**: three layers — pure unit tests on `InMemory*` repos (core, contracts, apps), Postgres
  integration tests (`storage/postgres/tests`, auto-skip without DB; cleanup deletes tenants `test%`),
  Vitest w/ stubbed fetch (UI). Backend tests also parse `deploy/*.sh` scripts as text contracts.
- **Version** `0.2.0` hand-synced in `VERSION` (unreferenced!), root pyproject, package.json, and each
  package pyproject/`__init__.py`.

## 3. Architecture

```
files dropped in storage/files/{tenant}/ingest/  (+ ingest.enhanced/ for heavy re-OCR, paddleocr only)
  → worker scan (StabilityTracker) → process_file(): move→hash→MIME(libmagic)→dedup(sha256)→security
  → extract_document() (text/md direct; office→Gotenberg→PDF; PDF per-page embedded-text vs OCR w/
    LLM quality judge) → artifacts in docs.active/{doc_id}/ (original.<ext>, manifest.json, content.md,
    content.json, pages/, normalized/searchable.pdf, thumbnails/thumb.webp) → document row →
    inline chunk+embed+entities → ACTIVE (failures → docs.failed|duplicates|quarantine)
  → FeatureReconciler drains document_features ledger (SKIP LOCKED, leases, backoff, DAG:
    extract → chunk_embed/entities/ner/doc_metadata/doc_classify/structured_records/thumbnail
    → entity_graph → relations) — everything re-derivable from artifacts
query path: question → deterministic shortcuts (count, aggregation router, looks_relational graph gate)
  → HybridPostgresRetriever (pgvector cosine + FTS 'simple' tsconfig, RRF k=60) → rerank
  (QwenReranker local or LlmReranker) → DefaultRagAnswerer grounded answer + citations + refusal;
  agent mode = hand-rolled tool loop (max 6 iters) or LangGraph multi-agent (backend orchestration/graph.py)
```

- **Two DI styles**: backend uses `doktok_core.registry.Registry` + `get_*` getters in
  `dependencies.py` (lazy, registry-first so tests can inject fakes); worker wires plain constructors in
  `composition.py::build_services`. MCP wires in `server.py::build_server`.
- **Feature ledger (ADR-0009)** is the backbone of derived data: versioned idempotent processors,
  delete-then-rebuild per doc, disjoint entity-type ownership (`entities` regex vs `ner` NER vs lexical
  CUSTOM_TOKEN). Version bump ⇒ corpus-wide backfill.
- **Staged ingestion (ADR-0015)** exists but `DOKTOK_STAGED_INGESTION=false` by default — inline
  `_activate` is the production path; keep both consistent.
- **Tenancy**: `tenant_id` on every row except global `app_settings`; tenant from credential, never
  request input. Static `DOKTOK_TENANT_TOKENS` map = fallback tier behind DB `api_tokens`; user-less
  tenant token ⇒ admin by design.
- **Settings split**: env (`DOKTOK_*`, pydantic `Settings` in `core/doktok_core/config.py`, ~80 vars,
  reads `.env`, `extra="ignore"`) seeds; DB `app_settings` holds runtime AI/OCR/egress config (per-purpose
  models: pipeline/embedding/rag/ner/keg/rerank) — applied on restart, some live-reloaded by worker
  (signature-based `ai_reload`, `ocr_reload` every 15s).

## 4. Package notes

### contracts/ (`doktok_contracts`) — leaf, zero behavior
- `schemas.py` (1909 ln, ~130 Pydantic models), `media.py` (18 dataclasses for hot paths: OcrPageResult,
  RenderedPage, TextChunk, ExtractedEntity/Relation, LlmUsage, ChatChunk…), `ports.py` (**54
  @runtime_checkable Protocols**: 19 repos incl. huge KnowledgeGraphRepository; extraction; AI providers
  as optional-capability protocols callers isinstance-check; Retriever/Reranker/RagAnswerer;
  FeatureProcessor; SecurityPolicy), `errors.py` (DuplicateActiveDocumentError).
- Conventions: additive-only schema evolution (new optional fields, never breaking — old JSONB rows must
  validate); StrEnum vocabularies; money = integer minor units; no re-exports/no `__all__` (deep imports
  everywhere — renaming a module is repo-wide). Dead-but-kept EntityType values (DATE/MONEY/*_ID, #312).
- UI hand-mirrors schemas in `apps/ui/src/api.ts` — drift risk, no codegen.
- Gotcha: pydantic undeclared in its pyproject.

### core/ (`doktok_core`) — domain logic, ~13.4k LOC, 119 modules
- `config.py` (Settings, THE env surface), `registry.py` (DI, docstring stale "M0 skeleton" but real),
  `logging_setup.py` (contextvars + secret redaction).
- `ingestion/` pipeline.py (process_file, IngestionServices, recover_stale_jobs), layout.py
  (FilesystemLayout), stability.py, extract_stage.py (ADR-0015 staged extract).
- `extraction/` service.py (MIME router, NeedsOcrError, NUL-strip), judge.py (LLM page-quality judge),
  quality.py (0..1 heuristic).
- `indexing/chunker.py` FixedWindowChunker 1200 chars/200 overlap.
- `features/` processors.py (9 features: ChunkEmbed v2, Entities v4, Ner v1, EntityGraph v1,
  RelationExtract v1, DocMetadata v2, DocClassify v2, StructuredRecords v2, Thumbnail v1), catalog.py
  (FEATURE_CATALOG/FEATURE_GROUPS = source of truth), reconciler.py, telemetry.py.
- `entities/` extractor.py (regex EMAIL/URL), validated.py (PHONE/IBAN/VAT_ID…), address.py (libpostal
  probe), ner.py (NER owns PERSON/ORG/GPE/JOB_TITLE; normalize_ner_name token-sort), language.py
  (langdetect→`doktok_kw_*` tsconfig), lexical.py.
- `knowledge_graph/` resolve.py (uuid5 canonical ids; FUZZY_RESOLUTION_ENABLED=False Phase-2),
  entity_resolution.py (4-stage cascade token_set/subset/typo/fuzzy_trgm), adjudication.py (LLM only for
  fuzzy_trgm), alias.py, predicates.py (closed vocabulary — never duplicate), name_parts.py, retrieval.py
  (DefaultGraphRetriever), evaluation.py.
- `rag/` answerer.py (DefaultRagAnswerer), reranker.py (listwise fallback), capabilities.py,
  evaluation.py. `agent/` loop.py (framework-free tool loop), merge.py, trace.py. `tools/` ToolGateway +
  library.py (6 read-only tools, prompt-injection fence).
- `aggregation/` router.py/counting.py/records.py/windowing.py (16k windows). `chat/`
  memory.py/summary.py(KEEP_RECENT=8)/title.py. `security/` auth/sessions(HS256 JWT)/passwords(scrypt)/
  roles/policy/egress(EgressBlocked stub). `settings/` catalog.py(MODEL_CATALOG, REASONING_LEVELS,
  ollama_think_for)/bootstrap.py(seed_ai_settings)/runtime.py/ocr_recommend.py. `audit/logger.py`
  (record_activity never crashes). `backup/` export.py/restore.py ("most dangerous feature", adversarial
  archive validation; uses subprocess pg_dump/openssl from core deliberately)/schema.py/fingerprint.py.
  `documents/` artifacts.py/repair.py. `enrichment/`, `visualizations/`, `tenants/provisioning.py`,
  `dev/seed.py`. Every submodule has `inmemory.py` mirroring Postgres semantics — **change port semantics
  ⇒ change inmemory + postgres together**.
- Third-party: pydantic(-settings), langdetect, phonenumbers, nameparser, PyJWT (transitive!).

### apps/backend/ (`doktok_api`) — only HTTP surface, FastAPI
- `main.py` create_app + middleware (request-id, limits: maintenance-sentinel 503, body caps, rate limit,
  metrics) + `/health` `/ready` `/metrics`. `dependencies.py` (763 ln) = wiring heart: require_tenant/
  require_user/require_admin, lazy `_get_database` (pool + migrate + seed), ~20 `get_*` DI getters.
- 17 routers in `routers/`: auth (login throttled, scrypt, JWT), admin (tenants/users/tokens/invitations,
  plaintext token shown once), preferences, ingestion (upload→tenant ingest/), documents (largest:
  keyset list, ids, detail/content/layout/page-image/entities/records/features/categories/file/thumbnail,
  retry/reprocess/reingest/rotate/delete), entities (KG curation: merge-suggestions+LLM adjudication,
  family-suggestions, merge/split/decompose/rename — static routes BEFORE `/{entity_id}`), chat (threads
  CRUD, /chat, /chat/stream SSE, /chat/retrieve, memories), settings (1309 ln: AI catalog+settings
  egress-gated, OCR settings+recommendation, DRP status/drill, portable backup export/restore),
  search/aggregate/audit/stats/tokens/categories/features/visualizations.
- `orchestration/graph.py` LangGraph multi-agent chat.
- Gotchas: undeclared lazy deps (provider_openai, provider_reranker); `get_tenant_registry` flips auth
  behavior mid-process; settings are deployment-global not per-tenant; sync handlers + slow LLM ⇒
  `API_DB_POOL_SIZE=10`; SSE persists assistant msg after stream (disconnect loses turn); in-process
  rate limiter/metrics (single replica); reads doc files directly from `storage_path` (shared volume with
  worker); destructive host actions via request FILES consumed by root systemd path units, never exec'd.

### apps/worker/ (`doktok_worker`) — ingestion/enrichment daemon
- `main.py` CLI `doktok-worker` (+ `repair [--check-hashes]`, `quiesce [--off]` — argv-sniffed, no
  argparse). `worker.py` IngestionWorker: 3 daemon threads (ingest 1s poll, reconcile 2s/100ms,
  projection 5s) + stale-job recovery 60s + heartbeat 15s (read by backend /ready).
- `composition.py` (~840 ln) composition root: per-tenant IngestionServices (+ enhanced twin when
  paddleocr), `_resolve_ner_backend`/`_resolve_relation_backend` (ADR-0023, GLiNER probe via find_spec,
  EgressBlocked stubs), signature-based live `ai_reload`/`ocr_reload`.
- Dockerfile bakes PaddleOCR+RapidOCR; non-root uid 10001; `stop_grace_period: 60s`.
- Gotchas: undeclared openai dep; OCR engine & reconcile concurrency are startup-only (restart needed);
  enhanced intake only for paddleocr; repair() uses env-only tenant map (skips DB tenants); OCR pool
  workers ~1 GB each — shutdown() or orphans.

### apps/mcp/ (`doktok_mcp`) — read-only MCP server (stdio, FastMCP)
- 3 tools: `search_documents` (hybrid via Ollama embed), `list_documents`, `aggregate_records`
  (hardcoded sum). Tenant pinned at build from `DOKTOK_MCP_TENANT` (never a tool arg); optional
  `DOKTOK_MCP_DATABASE_URL` for SELECT-only role (defaults to full DSN — hardening is opt-in). Never
  migrates. Gap vs plan: 7 tools promised, no audit/auth per caller. Neither MCP env var is in
  `.env.example`.

### apps/ui/ (`@doktok/ui`) — React 18 + Vite SPA, ~23.6k LOC flat `src/`
- Token-free bundle: Vite dev proxy injects `DOKTOK_DEV_TOKEN` only if SPA sent no Authorization; prod
  Caddy injects `DOKTOK_API_TOKEN`. Hash routing (no router lib), no state library, mount-don't-unmount
  (chat keeps streaming across tabs). `session.ts` monkey-patches window.fetch (401→login).
- Panels: Overview (upload drop-zone), Documents (1887 ln, list/thumbs, bulk ops), DocumentDetail (1043),
  Insights (KnowledgeGraph 2207 ln, EmbeddingMap, WordCloud×2 engines, Categories, Memory), Chat (1707 ln,
  SSE), Activity, Settings (2147 ln), Admin (775). `api.ts` (2349 ln hand-written client + DTOs + typed
  errors), `styles.css` (6748 ln monolith), persist.ts (localStorage + batched server pref sync).
- Constants hand-mirrored from backend (confidence thresholds, palette, caps). Dead: explore rail mode,
  several api.ts exports; old "Entities/Search tabs" gone (now Insights/Chat).
- Tests: vitest colocated, stub fetch, no test server.

### providers/ — 7 adapter packages (heavy runtimes as opt-in `[engine]` extras, lazy imports)
- **ollama**: OllamaChatModelProvider (chat+stream+tools+usage; tools force think=False), embeddings,
  vision OCR, metadata/classify/records/NER/relations extractors, adjudicator (not re-exported).
  think/reasoning quirks: structured extractors keep think on via `/no_think` prefix; `"a3b" in model`
  sniff decides repair think — fragile.
- **openai**: SDK-free httpx client (Responses API streaming w/ blocking fallback; global
  `DOKTOK_OPENAI_MAX_CONCURRENCY` semaphore read once at import, held for whole stream/retries); same
  extractor family; error taxonomy w/ retry (auth fails fast).
- **paddleocr** (default engine, PP-OCRv5; Enhanced = 4-way orientation vote ~4× cost, process pool,
  BLAS=1) & **rapidocr** (ONNX/OpenVINO; N95-recommended; `enable_mkldnn=False` needed on Alder Lake-N —
  oneDNN crash).
- **projection**: SklearnEmbeddingProjector PCA→HDBSCAN→UMAP, prewarm.
- **gliner**: GlinerEntityNerExtractor/NuNer + GlinerRelexRelationExtractor (default relations backend,
  ADR-0023); vendored `refiners/`+`kg_refiners/` verbatim (mypy-exempt; graph_writer/benchmark/fallback
  are dead-but-kept).
- **reranker**: QwenReranker (P("yes") calibrated; never raises, falls back to input order). Default
  AiSettings picks qwen-reranker but root depends on it WITHOUT [engine] ⇒ fresh install silently falls
  back to LLM reranker until `make reranker-models`.
- Gotchas: ollama/openai relations.py import `doktok_core` undeclared; rapidocr tests NOT in root
  testpaths (never run in CI); ollama relations untested; OCR page truncation at num_predict is log-only.

### storage/
- **postgres**: `Database` (psycopg pool wrapper, autocommit=True ⇒ explicit `conn.transaction()` for
  multi-statement), `migrate()` = custom ordered-SQL runner (51 migrations, NOT Alembic, advisory lock
  778130, idempotent, no down-migrations), `repositories.py` (3644 ln, 18 repos; KG repo ~960 ln w/
  merge/split reversible via canonical_id + merge log, pg_trgm similarity cascade), `crypto.py` (Fernet
  from sha256(SECRETS_KEY), `enc:v1:` marker). Work queues via `FOR UPDATE SKIP LOCKED`. Extensions:
  vector(HNSW), pg_trgm, 14 `doktok_kw_<lang>` FTS configs. Tests are real-PG integration, wipe `test%`
  tenants (careful naming tenants!).
- **filesystem**: LocalFileStorage (atomic os.replace + fsync file & dir — bytes durable BEFORE db row,
  APP-C1), Sha256HashService, QuarantineService. FilesystemLayout lives in core. Its tests are NOT in
  testpaths.
- **storage/files/{tenant}/**: ingest, ingest.enhanced, in.process, docs.active, docs.failed, duplicates,
  quarantine (gitignored runtime data).
- Gotchas: storage-postgres imports core undeclared (layering inversion import-linter can't catch);
  `audit_events` legacy (writes go to `document_activity`, which deliberately has NO FK to documents);
  migration 0050 dropped kg_entities unique constraint by design; test conftest wipes `test%` tenants on
  fallback DB URL.

### retrieval/hybrid — HybridPostgresRetriever (2 files)
- vector + FTS('simple' — must match generated tsv column) RRF k=60 fusion; `_filter_sql` category clause
  self-validating (unknown category ⇒ no-op filter, prevents false refusals — don't "fix"). Adapter→
  adapter dep on storage-postgres. Integration tests live in storage/postgres/tests.

### modalities/files — 8 file ports
- LibmagicMimeDetector (content not extension), DirectTextExtractor, PyMuPdfTextExtractor/Classifier
  (largest-image coverage), GotenbergNormalizer (office→PDF, no retry), PyMuPdfRenderer/Thumbnailer
  (WebP), SearchablePdfBuilder (image + invisible text layer, DPI-safe point sizing), rotate_source.
  Lazy native imports (fitz/magic/PIL) inside methods — preserve. Undeclared httpx dep.

### tools/builtin, tools/mcp — placeholder stubs only (`__init__.py`). Real tools live in
  `core/doktok_core/tools/`; real MCP in `apps/mcp`. Don't add imports expecting content.

### deploy/ — bash DRP engine + prod assets
- `lib.sh` shared (write_status atomic sentinels, log_event hash-chained history.jsonl). Orchestrators
  backup.sh (restic files + pgBackRest pg, full|diff|incr) / restore.sh (DESTRUCTIVE). Legs:
  backup-files (restic 14d/8w/6m), backup-pg (pgBackRest + WAL archive 60s timeout ⇒ ~1min RPO),
  backup-pg-logical (weekly pg_dump keep-4), azure-provision/sync (offsite Blob, immutability),
  check-backup-freshness, pg-wal-freshness. Restore: restore-pg (PITR), restore-files, restore-import
  (portable restore, root, safety snapshot→quiesce→pg_restore→files swap→maintenance off). Drills:
  restore-drill(weekly)/test-pitr/restore-roundtrip/drp-selftest/verify-recovery-dev/restore-drill-dev.
  deploy-to-box.sh (rsync+rebuild), install-systemd.sh (6 unit pairs; other documented units are
  examples only), ollama-autostop.sh.
- Contract with backend: `<BACKUP_DIR>/status/{files,pg,offsite,drill}.json` + `history.jsonl` (read),
  `status/requests/{drill,restore}.request` (backend writes, systemd .path fires root oneshots),
  `maintenance.flag`, `restore.json`. `DOKTOK_DEPLOY_MODE=host|compose` branches everything.
- Gotchas: host mode writes pgbackrest.conf WITH cipher pass into BACKUP_DIR which azure-sync uploads
  (key next to ciphertext); .path units hardcode `/var/lib/doktok/backups/...`; freshness thresholds
  assume 15-min host cadence (hourly compose timer can false-alert); restore-pg pins PG "17"; TABLES
  array duplicated across drill scripts; backup-runner image bakes deploy/ at build time.

### docs/ — knowledge base
- architecture/doktok-ng-architecture.md (master, 20 §§; "Proposed" status is stale), architecture/
  knowledge-graph-entities.md (KG identity invariants — read before touching entity identity/merges!).
- adr/ ADR-0001…0024 (24): 0001 ports&adapters; 0002 PG+pgvector spine; 0003 Ollama runtime; 0004 folder
  ingestion+DB jobs; 0005 hybrid retrieval; 0006 no-egress; 0007 tenant_id scoping; 0008 token auth;
  0009 feature reconciliation; 0010 PaddleOCR default; 0011 enrichment features; 0012 FK cascades; 0013
  keyset list contract; 0014 runtime per-purpose AI selection; 0015 staged ingestion (off by default);
  0016 embedding map; 0017 unidentifiable marker; 0018 conversational RAG workflow; 0019 Gotenberg
  office; 0020 N95 hybrid topology; 0021 pluggable OCR engines (partially stale — RapidOCR shipped);
  0022 agentic chat tools; 0023 pluggable NER/relation backends (benchmarks); 0024 tenant/user mgmt+RBAC.
- operations/ runbooks: running, testing, performance-and-ollama, deployment-trigkey-n95,
  deploy-fresh-box-runbook, security-runbook, backup-and-recovery, backup-restore-consistency, rag-eval.
- prompts/doktok-ng-original-brief.md = founding spec (1863 ln, "DokTok2").
- Doc drift: M0-M10.md roadmap most stale (trust N95 deployment guide); ADR statuses unreliable;
  some dangling refs to ~/.claude/agent-memory.

### eval/ + scripts/
- eval/corpus = 7 tiny synthetic German-household docs; golden.json (12 RAG cases incl. refusal,
  aggregation, relational), golden_entities/edges/enrichment.json. Harnesses run LIVE config, no
  fallback, throwaway `eval` tenant (wiped before+after — never name a real tenant "eval"; also avoid
  `test%`). Make targets rag-eval/enrich-eval/ner-bench/kg-bench; metrics logic in core (unit-tested),
  scripts are thin composition roots.
- scripts: `_x.py` + `x.sh` wrapper pairs; create-tenant/clean-tenant/seed-dev (guarded: loopback,
  confirm, env-passed tenant id, bound params); preflight models (derives from MODEL_CATALOG; emits Make
  target names — renaming targets breaks it); restart-ollama.sh (NUM_PARALLEL=4 etc.); CI
  detect_secrets_check.py (hash-based diff vs .secrets.baseline, ignores line drift; rewrites baseline
  in place during scan).
- Gotcha: clean-tenant misses all `kg_*` tables (KG survives tenant wipe); eval runners always exit 0.

## 5. Editing conventions (match these)

**Per-ticket workflow (user-mandated, 2026-07-18):** plan → present → wait for user ok → create
branch `fix/<slug>` + link it to the issue (GraphQL `createLinkedBranch`) + set Roadmap project
Status = In Progress → implement (TDD) → `make check` green → ask before any commit/PR → on
merge/close set Status = Done. Never commit or push without an explicit ok each time.

- Comments/docstrings carry ADR + milestone + issue refs (`ADR-0022`, `M13`, `#371`, `APP-C3`) — keep
  accurate; explanatory-why style. No TODO/FIXME markers anywhere — deferred work lives in docstrings.
- Conventional Commits; branch `mX/<slug>` / `fix/<slug>` / `docs/<slug>`; PR template checklist = make
  check. Milestone-driven; every milestone ships runnable.
- New derived data ⇒ new versioned FeatureProcessor + FEATURE_CATALOG entry, not ad-hoc jobs.
- New tenant-owned table ⇒ tenant_id + tenant-leading indexes + add to `_TENANT_TABLES`/`_EVAL_TABLES`/
  drill TABLES lists. Migrations: append-only numbered .sql, idempotent, header cites milestone/ADR.
- Schema changes: additive optional fields only (contracts tests enforce); mirror TS types in
  `apps/ui/src/api.ts` by hand; update inmemory + postgres repos together.
- LLM-facing logic: deterministic gate/normalizer first, model second, graceful degradation always;
  prompts treat document text as untrusted DATA.
- Secrets: never in files/args (env only); detect-secrets scans docs too (`# pragma: allowlist secret`).
- mypy strict; ruff 100 cols; `from __future__ import annotations`; py.typed per package.
- Deferred heavy imports inside functions (module import must never need DB/Ollama/native libs).

## 6. Sharp risks to remember

1. Undeclared cross-package deps everywhere (contracts→pydantic; storage-postgres→core; worker/backend/
   providers→openai/reranker/core; modalities→httpx) — fine in workspace, broken as standalone wheels.
2. `test%`/`eval` tenant names get wiped by test/eval harnesses; clean-tenant misses kg_* tables.
3. Reranker default needs `make reranker-models` (not in lockfile); same for OCR engines/NER/projection.
4. Freshness check vs compose timer mismatch can false-alert; watchdog fails open.
5. Host-mode pgBackRest cipher pass ends up in Azure next to the repo it encrypts.
6. Portable restore is "the most dangerous feature in the app" — validation is load-bearing.
7. Backend settings are deployment-global; any tenant admin can change AI/OCR/egress for all.
8. CI mypy narrower than local; rapidocr + storage/filesystem tests not in testpaths (invisible).
9. `VERSION` file unreferenced; version hand-synced in ~5 places.
10. UI mirrors backend constants/schemas by hand — silent desync possible.

## 7. Runtime quick reference

- Dev: `make db` → `make run-backend` (:8000) / `make run-worker` / `make run-ui` (:5173). Dev tokens:
  `dev-token-default`→tenant `default` (UI proxy), `dev-token-developer`→tenant `developer`.
- Defaults: chat `qwen3.6:35b-a3b` (Ollama), embeddings `qwen3-embedding:0.6b` (1024-dim), OCR
  `paddleocr` (prod: rapidocr/openvino), Gotenberg :3000, PG :5433 (default; DOKTOK_DB_PORT override).
- Prod: docker-compose.prod.yml on Trigkey N95 (8 GB; caps ≈6.5 GB); Caddy only published port; hybrid
  split (local OCR+embeddings, OpenAI enrichment/chat per ADR-0020); deploy via `make deploy-box` or
  manual `deploy.yml`; backups restic+pgBackRest+Azure with sentinel/history contract.
- Login opt-in via DOKTOK_AUTH_JWT_SECRET; `make seed-dev` creates dev tenant w/ 3 role users.
