# Changelog

All notable changes to DokTok NG are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Tenant & user management with login and RBAC (EPIC #523 + login flow).** A DB-backed
  tenant/user/api-token registry; opt-in password login issuing a short-lived session JWT
  (`POST /api/v1/auth/login`, gated on `DOKTOK_AUTH_JWT_SECRET`, falling back to
  `DOKTOK_SECRETS_KEY`; token-free/proxy mode when unset); role-based access control
  (viewer/editor/admin) enforced per route; invitations + immediate deactivation; per-user
  server-side preferences; an Admin tab (members, roles, invites, API tokens, tenants with
  server-generated GUID ids); and audit attribution of actions to the authenticated user. Login is
  brute-force throttled, passwords follow a length policy, and a real in-browser login screen keeps
  the JWT in memory + sessionStorage. Dev seeding via `make seed-dev` (gated to local/dev). See
  [ADR-0024](docs/adr/ADR-0024-tenant-user-management-and-rbac.md), the dev walkthrough in
  [docs/operations/running.md](docs/operations/running.md), and
  [docs/operations/testing.md](docs/operations/testing.md).

### Security
- **No-egress is now configurable from the UI, with an operator hard-lock.** A toggle in Settings →
  AI sets the posture (persisted in-app, seeded from `DOKTOK_NO_EGRESS`); turning it off (allowing
  egress) requires a confirm and is audited. A new `DOKTOK_NO_EGRESS_LOCK` env hard-locks it on and
  disables the toggle ("Enforced by the host") for hardened deployments. Effective value =
  `effective_no_egress(in-app toggle, env default, host lock)`, read by the API, both runtime sinks,
  and the readiness probe; turning it off + selecting OpenAI in one save validates against the new
  posture. Disabling a locked posture returns `422 no_egress_locked`.
- **No-egress gate is now enforced end-to-end, visible, and fail-closed** (#413 boundary/read, #414
  sinks, UI gate). Previously `DOKTOK_NO_EGRESS` was enforced only at the worker, silently: the UI
  let you pick OpenAI under no-egress, the save was accepted, then the worker quietly fell back to a
  local model (or failed with a cryptic `Connection refused`). Now: "egress" means **any non-loopback
  destination** (OpenAI **or** a remote Ollama URL — the latter was ungated before) via one
  `purpose_requires_egress` predicate; `PUT /settings/ai` **422s** a selection that would egress
  under no-egress with per-field reasons; `GET /settings/ai` + the catalog expose `no_egress` and
  per-purpose/`per-option` egress status; the Settings UI **disables** forbidden models with a clear
  reason, shows a posture badge, and distinguishes "blocked by no-egress" from "needs an API key";
  the worker and RAG sinks **fail loud** (no silent substitution, no remote-URL egress); and enabling
  egress is audited (`egress.enabled`).

### Fixed
- **Overview "Processing" count stayed 0 during feature reprocessing.** It summed only ingestion
  job statuses, but a re-scheduled extraction runs on an already-`active` document (terminal job) and
  its work lives in the feature ledger. `StatsSummary` now reports `documents_processing_features`
  (documents with a `pending`/`running` feature) and the Overview folds it into "Processing", so
  reprocessing is visible instead of reading 0.

### Added
- **Documents list: "select all matching" across pages** for bulk actions. After the header
  checkbox selects the loaded page, a banner offers "Select all N matching" the current filter (via
  the existing `GET /documents/ids`, capped at 10,000 with honest "first 10,000 of N" copy when more
  match). Lets you reprocess a failing feature over a large filtered set without paging manually.
  Bulk actions now run with bounded concurrency (6 in flight) + live progress instead of one
  unthrottled request per document; selecting-all-matching survives the 4s poll (the prune effect is
  suppressed in that mode); deselecting a row or changing a filter exits the mode.
- **DRP hardening: tamper-evident backup history + live restore drills** (#396 backend, #397 UI).
  Backups now append to an authoritative, **append-only event history** at
  `$DOKTOK_BACKUP_DIR/status/history.jsonl` (outside Postgres, so a DB restore can't roll it back),
  written by `log_event` in `deploy/lib.sh` (compose wrapper `deploy/log-event.sh`): one JSON line per
  event with a `seq` + `prev_sha256` **hash chain**, flock-serialized appends, field-whitelisted +
  JSON-escaped `detail` (no secrets), rotating at ~5000 lines. The freshness sentinels are unchanged
  (latest-state); the history is the timeline. New `GET /api/v1/settings/drp/history?limit&leg`
  (`DrpHistoryResponse`) is a bounded, newest-first tail read that verifies the chain
  (`integrity_ok=false` signals tampering) and never 500s; the Settings → DRP tab gained a
  backup-history table with a leg filter, empty/truncated states, and an integrity-failure banner.
  Backup/drill outcomes are also **mirrored** into the in-DB activity log (new `AuditEventType`
  values `backup.completed` / `backup.failed` / `drill.completed`), explicitly non-authoritative and
  idempotent — the history file remains the source of truth. `deploy/restore-drill.sh` is now a
  **live, hardened drill** (asserts restored row counts, records measured RPO/RTO + an evidence
  string, throwaway-only): a weekly `doktok-restore-drill` timer plus an on-demand path
  (`POST /drp/drill` drops a request file consumed by a root `doktok-restore-drill-ondemand.path`
  unit under `flock`; the backend never execs root; 429 on pending/cooldown), with a "Run drill now"
  button in the UI. Azure offsite remains deferred / out of scope.
- **`make deploy-box` one-command deploy** (`deploy/deploy-to-box.sh`): rsyncs the working tree to the
  compose box, rebuilds the images on the box with live build progress, runs
  `docker compose ... up -d`, and prints health. Configurable via `DOKTOK_BOX_HOST` / `DOKTOK_BOX_KEY`
  / `DOKTOK_BOX_DIR` / `DOKTOK_BOX_SERVICES` (and `DOKTOK_BOX_NO_BUILD=1` to skip the rebuild). Fails
  fast if the box is missing `/opt/doktok/.env.production`. Completed the `.env.production.example`
  template into a sectioned single source of truth (REQUIRED vs optional, secret-generation hint, real
  N95 settings) and refreshed the install docs (fresh-box runbook, N95 deployment guide, README,
  security runbook) for the one-command redeploy path and the RapidOCR/OpenVINO OCR default.

### Changed
- **OCR-quality judge now follows the Data Pipeline AI setting instead of a hardcoded model.**
  Previously, when the Data Pipeline ran on OpenAI the embedded-vs-OCR judge silently loaded a
  separate local `qwen3:14b` (via the UI-invisible `DOKTOK_JUDGE_MODEL`/`DOKTOK_ENRICH_MODEL` env
  defaults) for every ambiguous page during ingestion. The judge now uses the same provider+model as
  the Data Pipeline extractors: an OpenAI pipeline sends the judge's tiny A/B prompt to OpenAI too
  (no local model loaded), a local Ollama pipeline reuses the already-resident pipeline model. Removed
  the now-unused `DOKTOK_JUDGE_MODEL` / `judge_num_ctx` config. `DOKTOK_ENRICH_MODEL` remains only as
  the local fallback used when the pipeline is OpenAI but egress is disabled.
- **Document enrichment now also follows the Data Pipeline AI setting; no hardcoded enrichment
  model.** The metadata/category/record/NER extractors no longer fall back to a separate
  `DOKTOK_ENRICH_MODEL` (which defaulted to `qwen3:14b`); when the Data Pipeline is set to OpenAI but
  egress is disabled they fall back to the system `default_model`, exactly like the judge. Removed the
  `enrich_model` config and the `DOKTOK_ENRICH_MODEL` / `DOKTOK_JUDGE_MODEL` env vars from all
  templates. The default pipeline model and the selectable model catalog dropped `qwen3:14b` in favour
  of `qwen3.6:35b-a3b`, so no purpose loads `qwen3:14b` unless an operator explicitly selects it.

## [0.2.0] - 2026-06-26

### Added
- **DRP backup metrics + redesigned status panel** (#380): backups now capture per-leg metrics into
  the freshness sentinels at `$DOKTOK_BACKUP_DIR/status/<leg>.json` — for the files leg `file_count`,
  `size`, and the restic snapshot `backup_id` (parsed from `restic backup` output in
  `deploy/backup-files.sh`); for the pg leg the database `size` and the pgBackRest `backup_id`
  (label), parsed from `pgbackrest info --output=json` by the `pg_backup_extra` helper in
  `deploy/lib.sh`. `write_status` gained an optional metrics fragment arg and a `WRITE_STATUS_TS`
  recovery-point override. The contract (`BackupLegStatus`) and `GET /drp` now carry
  `size`/`file_count`/`backup_id`, and the Settings → DRP tab was redesigned into per-leg status
  **cards** with colour-coded state badges (green ok / amber stale / red failed / grey unknown),
  last-run age, the captured metrics, and a WAL-lag note.
- **systemd backup timers + per-minute pg WAL-freshness stamp** (#377): shipped units in
  `deploy/systemd/` — `doktok-backup-diff` (hourly → `deploy/backup.sh diff`), `doktok-backup-full`
  (weekly Sun 03:00 → `deploy/backup.sh full`), and `doktok-pg-wal-freshness` (every minute →
  `deploy/pg-wal-freshness.sh`) — installed by the new `deploy/install-systemd.sh` (run as root,
  reads `/etc/doktok/backup.env`, now including `DOKTOK_DEPLOY_MODE`). `pg-wal-freshness.sh` stamps
  the pg sentinel's `last_run_at` to the last archived WAL time and records `wal_lag_s`, preserving
  the base backup's `size`/`backup_id`, so the pg leg reflects its ~60s WAL RPO instead of flapping
  "stale" between hourly/weekly base backups. The prod db now sets `archive_timeout=60`
  (`docker-compose.prod.yml`) so an idle DB still ships a WAL segment each minute.

### Fixed
- **pgBackRest runs as the `postgres` user in compose mode** (#383): `deploy/backup.sh` uses
  `docker compose exec -u postgres ... pgbackrest` rather than the default root. Running as root
  rewrote the repo's `archive.info` as root-owned `0640`, after which the WAL `archive_command` (the
  postgres server uid 999) could not read it and all WAL archiving failed with `WAL segment ... not
  archived before the 60000ms timeout`. Recovery on an affected box: `sudo chown -R 999:999
  $DOKTOK_BACKUP_DIR/pg`.

- **Office-document support (`.docx`/`.xlsx`/`.pptx`)** (#313): OOXML documents are added to the MIME
  allowlist and converted to PDF **on ingest** by a **local Gotenberg container**
  (`gotenberg/gotenberg:8`, MIT-licensed) wrapping headless LibreOffice, then run through the existing
  PDF extraction / render / OCR / thumbnail / preview path. Conversion is fully local; document
  content never leaves the host. New `DocumentNormalizer` port + `GotenbergNormalizer` adapter; new
  `gotenberg` service in `docker-compose.yml`; new settings `DOKTOK_GOTENBERG_URL` (default
  `http://localhost:3000`) and `DOKTOK_GOTENBERG_PORT` (default `3000`). The converted PDF is the
  canonical viewable form (inline preview, "Open in new tab", thumbnails, page images, OCR overlay);
  **Download** returns the original office file, preserved byte-for-byte. (ADR-0019.)

### Changed
- **First-pages enrichment for cheap LLM features** (#311): the `doc_metadata` and `doc_classify`
  feature processors now read only the **opening pages** of a document (page-aware helper `_head_pages`:
  ~2 pages / ~6k chars for metadata, ~3 pages / ~8k chars for classification) instead of a flat
  character slice, cutting tokens, latency, and cost. Full text is still used for chunking/embeddings,
  the regex entity extractor, NER, and structured records. Feature versions were intentionally not
  bumped, so this applies to newly ingested / reprocessed documents, not a retroactive corpus re-run.

### Removed
- **Low-value regex entities** (#312): the rule-based extractor no longer emits MONEY, DATE,
  INVOICE_ID, CONTRACT_ID, or DOCUMENT_ID (their matches were ~90% noise; monetary data lives in
  extracted records, dates in metadata). It now emits only EMAIL and URL; NER still emits PERSON/ORG/GPE
  and the lexical extractor still emits CUSTOM_TOKEN. The enum values are kept for back-compat but
  marked not-extracted; migration `0030_drop_low_value_entities.sql` deletes existing rows of the five
  dropped types.

### Known issues
- **Long-document `structured_records` truncation** (#314): the structured-records extractor silently
  truncates very long documents to the first 16k characters, dropping the tail of transactions.
  Tracked as a follow-up; not yet fixed.

### Added
- **Settings-driven reasoning, end to end**: document interrogation (RAG chat) now honors
  `rag.reasoning` from Settings by default instead of being driven solely by the chat **Show
  reasoning** toggle (the toggle still overrides the setting per message). Root cause was
  `OllamaChatModelProvider.stream_complete` hardcoding `think=false`. The OCR-quality judge now
  applies the configured pipeline reasoning as well.
- **`DOKTOK_EMBEDDING_NUM_CTX` (default 1024)**: caps the per-call embedding context instead of using
  the model's 32k default. Chunks are ~300 tokens, so embeddings are unchanged; the cap frees GPU
  KV-cache. Wired into the worker, backend, and MCP paths.
- **Settings shows a read-only "Embedding (index)" display** (model + context). The embedding model
  is intentionally not user-selectable: changing it would alter the vector dimension and require a
  schema migration plus a full re-index.

### Changed
- **Ollama JSON-repair reuses the configured pipeline model**: the structured-output repair step now
  calls the same configured pipeline model instead of a separate repair model, so the pipeline model
  choice stays authoritative.

### Removed
- **`DOKTOK_ENRICH_REPAIR_MODEL` / `enrich_repair_model`**: the dedicated JSON-repair model config is
  gone now that repair reuses the configured pipeline model.

### Added
- **Stage dependencies in the feature ledger (ADR-0009)**: processors now declare `dependencies`,
  and `claim_next` only claims a stage once every prerequisite has a `done` row on the same
  document (gated in the SQL via the dependency edge set, and mirrored in the in-memory oracle). The
  groundwork for making intake/extraction first-class staged nodes; empty dependencies = no gating,
  so existing behavior is unchanged.
- **Parallel OCR**: PaddleOCR now runs a pool of independent predictors (one per ingestion slot)
  instead of serializing every `predict()` behind one lock, so pages OCR concurrently and use the
  available CPU. `DOKTOK_INGEST_CONCURRENCY` (default raised 2 → 4) sizes both the ingestion workers
  and the OCR predictor pool.
- **Structured aggregation, end to end (M6.3)**: a **Totals** tab issues a typed `AggregationIntent`
  to `POST /api/v1/aggregate` for deterministic SUM/COUNT over `extracted_records` (per-currency
  rollups + provenance), and the **chat** endpoint now routes total/count questions ("how much did I
  spend at X") to the same deterministic path — a keyword gate + LLM slot-fill build the intent, and
  any failure falls back to semantic RAG so chat never breaks.
- **Worker startup feature recovery**: `FeatureReconciler.recover_running()` requeues feature rows
  left `running` by a killed worker at reconcile-loop startup, so a restart no longer strands a
  document's feature for the full lease window.
- **Documents tab: List and Thumbnails views** (`DocumentsPanel.tsx`). The existing table is now the
  **List** view (default); a new **Thumbnails** gallery shows each document as a card with its
  first-page preview and overlaid selection / status / per-feature badges, with an S/M/L size control
  persisted to `localStorage`. Both views share one toolbar (sort, token-filter chips, status /
  category / needs-attention) and one multi-select model (individual, shift-range, and select-all-
  matching) with the same bulk actions.
- **Document thumbnails**: a versioned `thumbnail` `FeatureProcessor` (reconciled via the ADR-0009
  framework, registered in the feature catalog) renders the first page of each document's normalized
  PDF to a small WebP via the new `PyMuPdfThumbnailer` adapter (`modalities/files/.../render.py`, using
  `fitz` + Pillow — Pillow is now a core dependency of `modalities/files`). Stored at
  `docs.active/<id>/thumbnails/thumb.webp` and served by `GET /api/v1/documents/{id}/thumbnail` (404 →
  placeholder until rendered). The document detail card now uses a two-column thumbnail + summary
  layout.
- **Documents list API: sorting, token filtering, and select-all** (`GET /api/v1/documents`). New
  `sort` (`acquired` = ingestion time / `created` = the document's own date / `title` / `category`) +
  `dir`, plus token filtering (`token[]`, `token_match` = `all` (AND, default) / `any` (OR), optional
  `token_type`). The list is **keyset-paginated** with a self-describing opaque cursor (it encodes the
  sort + dir + value + id, sorts NULLs last, and returns 400 on a stale or mismatched cursor instead of
  silently mis-paging). New `GET /api/v1/documents/ids` returns every id matching a filter (capped at
  10k with a `truncated` flag) so "select all matching" can act on the whole result set, not just the
  loaded page. Backed by migration `0018_documents_list_sort_indexes.sql` (per-sort keyset indexes;
  keyset pagination itself landed in `0016_documents_keyset_pagination.sql`). New
  `DocumentRepository.list_document_ids`, extended `list_documents`, and the
  `DocumentSort` / `SortDir` / `TokenMatch` / `ListAnchor` / `DocumentIdSelection` contracts.
- **Settings tab: AI model selection** (`SettingsPanel.tsx`, `routers/settings.py`,
  `core/.../settings/catalog.py`, `providers/openai/`). Choose the model per purpose — pipeline
  feature-extraction vs RAG / interrogation — across local Ollama and remote OpenAI, with a unified
  reasoning-density control (`off|low|medium|high`, mapped to each provider's knob) and a **write-only**
  OpenAI API key (never returned; GET reports only whether one is set). Persisted as global system
  settings in `app_settings` (migration `0017_app_settings.sql`) and applied on the next restart.
  Selecting an OpenAI model is an explicit, opt-in exception to the local-first / no-egress default
  (ADR-0006). New `GET /api/v1/settings/ai`, `GET /api/v1/settings/ai/catalog`, `PUT
  /api/v1/settings/ai`.
- **Overview dashboard redesign** (`OverviewPanel.tsx`): the document **library** (Documents /
  Entities / Categories counts) is now separated from an **Ingestion** pipeline section that shows only
  actionable states — Waiting / Processing / Failed / Pending features — plus a "Pipeline idle" message
  when nothing is in flight. The raw "Jobs" tile and the "Jobs by status" list are gone (the active-job
  count only duplicated the document count and invited a false comparison); in the Ingestion view a
  finished job's `active` status is relabelled **"ingested"**, so "active" only ever describes a
  document.
- **PaddleOCR (PP-OCRv5) is now the default OCR engine** (`DOKTOK_OCR_ENGINE=paddleocr`), replacing the
  glm-ocr vision model. PaddleOCR is a detection+recognition pipeline, so it **structurally cannot
  repeat-loop** into garbage on sparse/stamp pages (it returns no text instead) and provides **native
  per-line confidence**. Verified on real German scans: clean pages → ~0.98 confidence at ~13 s/page
  (mobile models); the sparse page that made glm-ocr emit hundreds of garbage lines → empty/low-conf.
  Output is kept shape-compatible (`OcrPageResult`: page text in reading order + mean confidence) so
  nothing else in the pipeline changes. The runtime is an **optional extra** (kept out of CI):
  `make ocr-paddle`. Set `DOKTOK_OCR_ENGINE=glm-ocr` to use the previous Ollama engine.
- **Bulk re-ingest** now works for documents of any status (not just failed). Selecting documents and
  choosing "Reingest selected" reads each original file, **fully purges** the document — its files and
  all derived rows (chunks, entities, features, category links, extracted records, jobs, now via DB
  `ON DELETE CASCADE`, migration 0013) — and drops the original back into the ingest folder so the
  worker reprocesses it cleanly (e.g. to redo an OCR that produced garbage). Both Reingest and Delete
  confirm first.
- Documents list management: filter by **status** (active / failed / duplicate) via
  `GET /api/v1/documents?status=…`, **select** documents individually or all (checkboxes +
  select-all), and run **bulk actions** on the selection — for failed documents, **Reingest selected**
  or **Delete selected**. New `DELETE /api/v1/documents/{id}` (removes the document, its files, and —
  via FK cascade — its records). Delete asks for confirmation.
- Structured aggregation, phase 1 (M6.3 `structured_records`): a versioned, idempotent
  `StructuredRecordsFeature` extracts **typed line items** (transactions: date, merchant, amount,
  currency, debit/credit) from financial documents into a queryable `extracted_records` table
  (migration 0012) — the foundation for answering questions top-k RAG can't, like "how much did I
  spend at Block House across all statements". Money is stored as **integer minor units** (never
  float) so SUM is exact; merchant names are normalized (`pg_trgm` index) for fuzzy matching; the
  extractor returns nothing for non-financial documents. The query router + aggregation intents +
  answer come in phase 2.
- Enrichment **title + summary now match the document's language** (e.g. a German contract gets a
  German title and summary), via an explicit instruction in the extraction prompt.
- Faster LLM calls by reducing "thinking": the RAG answerer, reranker, and OCR-quality judge now run
  with `think=false` (no structured `format` there, so it applies fully); the enrichment prompts use
  `/no_think` to soft-trim thinking on the qwen3.6 MoE (which can't hard-disable it alongside
  structured output). For a large enrichment speedup, a new `DOKTOK_ENRICH_THINK=false` paired with a
  dense `DOKTOK_ENRICH_MODEL=qwen3:14b` hard-disables thinking (that combo handles `think=false` +
  `format` correctly).
- **Retry ingestion** for failed documents: a failed document's detail card now shows a "Retry
  ingestion" button. `POST /api/v1/documents/{id}/reingest` moves the preserved original back into the
  tenant's ingest folder and clears the failed document + job records, so the worker reprocesses it
  cleanly on its next run. (Tenant-scoped, with a path-traversal guard; only `failed` documents are
  eligible.)
- Document-enrichment evaluation (M6.2): `make enrich-eval` ingests the golden corpus, runs the
  `doc_metadata` + `doc_classify` features against the real models, and scores title / document-date /
  location / category / summary against `eval/golden_enrichment.json`. Deterministic scoring lives in
  `core/doktok_core/enrichment/evaluation.py` (unit-tested in CI; the runner is local-only). Baseline:
  **4/4** documents pass all checks (titles are real, dates correct or NULL, categories deduplicate and
  reuse across documents). See `docs/operations/rag-eval.md`.
- Document enrichment, phase 3 (M6.2 categories UI): the **Documents** tab now has a **category
  filter** (a dropdown of the tenant's categories with counts; selecting one filters the list via
  `GET /api/v1/documents?category=…`), and the **Overview** dashboard shows a **"Documents by
  category"** breakdown. New `CategoryRepository.documents_for_category` + the document-list `category`
  query param. The clean-tenant script and test-tenant cleanup now also clear `categories` /
  `document_category_links` (no document FK to cascade). (A serialized taxonomy maintenance/merge pass
  remains an optional future enhancement; the inline trigram dedup + caps already keep the vocabulary
  bounded.)
- Document enrichment, phase 2 (M6.2 `doc_classify`): documents are now **multi-label categorized**
  from a **bounded controlled vocabulary** — at most 5 categories per document and 20 active per
  tenant, both enforced in the database via `BEFORE INSERT` triggers with per-group advisory locks
  (race-safe under concurrent workers), not trusted to the prompt. The LLM proposes labels; the worker
  resolves each against the live taxonomy (exact → trigram-fuzzy via `pg_trgm` → create if under the
  cap → else force-pick the nearest existing), so ingestion never blocks. New tables `categories` +
  `document_category_links` (migration 0011); a versioned, idempotent `doc_classify` `FeatureProcessor`
  backfills the corpus; `GET /api/v1/categories` lists the vocabulary with per-category counts and
  `GET /api/v1/documents/{id}/categories` returns a document's categories, shown as chips on the detail
  card. (Documents-list category filter, an Overview breakdown, and the serialized taxonomy
  maintenance/merge pass are the next phase.)
- Document enrichment, phase 1 (M6.2 `doc_metadata`): every document now gets an LLM-generated
  **title**, a **document date** (the date it's *about*; `n/a` when undeterminable), a **location**,
  a **summary**, and an explicit **ingestion timestamp**. Implemented as a versioned, idempotent
  `doc_metadata` `FeatureProcessor`, so the reconciler backfills the whole corpus and a version bump
  re-runs it. Extraction uses `qwen3.6:35b-a3b` with strict structured `format` output (thinking left
  on — never `think=false` with `format`) and a `qwen3:14b` JSON-repair fallback; all fields are
  **hard-validated in code** (ISO date or NULL, title word-cap, `n/a`→NULL). New columns on
  `documents` (migration 0010); the document detail view shows the title, a Summary block, and
  Document date / Location / Ingested (with "n/a" where unknown). Configurable via
  `DOKTOK_ENRICH_MODEL` / `DOKTOK_ENRICH_REPAIR_MODEL` / `DOKTOK_ENRICH_NUM_CTX`.
- RAG **LLM reranker** (M6.1): the answerer now retrieves wide (`DOKTOK_RAG_RETRIEVE_K`, default 40),
  has the chat model listwise-rerank the candidates in a single call, keeps the best `limit`, and packs
  them "edges-best" (most relevant at the start and end) to fight lost-in-the-middle. A new `Reranker`
  port + `LlmReranker` (falls back to retrieval order on any parse/model failure, so it can only
  improve retrieval). Plus a **citation guardrail**: answers now cite only the excerpts they actually
  referenced with a valid `[n]` index. (Adds one extra LLM call per chat query.)
- Per-feature processing badges are now surfaced in the document lists, not just the detail view: the
  **Documents** tab shows a chip per feature with its status (e.g. `chunk_embed ✓`, `entities …`,
  `entities ✗`) on each row, and the **Overview** dashboard shows a "Pending features" rollup
  (documents with any feature not done). `GET /api/v1/features` returns the tenant's ledger;
  `StatsSummary.documents_pending_features` drives the rollup. (`status` stays the lifecycle flag;
  features = enrichment coverage.)
- RAG evaluation harness (eval-first, M6.1): deterministic metric logic (`evaluate`: retrieval recall,
  answer correctness, citation correctness, refusal correctness; CI-tested with fakes), a golden corpus
  + Q/A set (`eval/`) tagged by kind (factoid / aggregation / refusal), and a local runner
  (`make rag-eval`) that scores the set against real Ollama models. Establishes a measured baseline for
  the embeddings/reranker work, and includes an aggregation ("beyond-RAG") case to track that gap.
  See `docs/operations/rag-eval.md`.
- Document card file actions (designed with the UI/UX agent): the document detail view now serves the
  raw file (`GET /api/v1/documents/{id}/file?variant=original|normalized&disposition=inline|attachment`
  with correct `Content-Type`, `Content-Disposition`, `X-Content-Type-Options: nosniff`, and byte-range
  support) and offers **Open in new tab** / **Download** (real anchors with `rel="noopener noreferrer"`)
  plus an accessible **Preview** overlay (native `<dialog>`: focus trap, ESC/backdrop close) that
  renders PDFs in an iframe, images, and text, with a fallback for unpreviewable types. Duplicate
  documents show a banner with an **Open original** button.
- Document feature reconciliation (ADR-0009), phase 1: a per-document, per-feature ledger
  (`document_features`, migration 0009) and a `FeatureReconciler` running in the worker drive every
  active document toward having every registered feature processed. New features backfill existing
  documents automatically; failures retry with backoff then record the error; a crashed run is
  reclaimed via a lease; processing resumes after restart. Designed for **multiple worker instances**:
  work is claimed atomically with `SELECT ... FOR UPDATE SKIP LOCKED`, so workers can be spawned under
  load without double-processing. `chunk_embed` and `entities` are registered as idempotent processors
  (re-derived from stored artifacts); a manual `reset` re-queues a feature. Each document's per-feature
  status is exposed at `GET /api/v1/documents/{id}/features` and shown in a **Processing** panel on the
  document detail view, with a **Retry** button (`POST /api/v1/documents/{id}/features/{feature}/retry`)
  for any non-done feature.
- The chat/RAG model (qwen) now runs with a configurable context window (`DOKTOK_CHAT_NUM_CTX`,
  default 32768) via `options.num_ctx`, giving RAG room for many retrieved chunks. OCR and embedding
  models are unaffected (they keep their defaults). Measured ~23 GB total for qwen at 32k thanks to
  grouped-query attention - comfortable on 48 GB.
- Failed and duplicate documents now get a `documents` record and keep their **original filename** on
  disk. Failed files are stored as `docs.failed/{job_id}/<original-name>` with a `status=failed`
  document row (error code/message in metadata). Duplicate files (re-ingest of already-active content)
  go to a new `duplicates/{job_id}/<original-name>` folder with a `status=duplicate` document row whose
  `duplicate_of` points to the original document; the job ends in a distinct `duplicate` status (was
  `failed`). Adds migration 0008 (`documents.duplicate_of`) and the `document.duplicate` audit event.
- Faceted **token search**: a chip-based search bar with autocomplete over the indexed tokens. Typing
  a prefix suggests matching tokens (case-insensitive), each selected token becomes a removable chip,
  and the next suggestion list is narrowed to tokens that co-occur in documents already matching the
  selection. Documents must contain ALL selected tokens (AND). New `GET /api/v1/tokens/suggest` and
  `GET /api/v1/tokens/search`, backed by `EntityRepository.suggest_tokens` / `documents_for_tokens`,
  and a **Token Search** UI tab.
- The ingestion worker can process multiple stable files **in parallel** (`DOKTOK_INGEST_CONCURRENCY`,
  default 4) for higher throughput. Stability tracking stays single-threaded; only the independent
  per-file pipelines run in a thread pool (the Postgres pool is thread-safe and each job has its own
  working directory). The worker's DB pool is sized to the concurrency.
- Overview dashboard now shows **"Waiting in ingest"** - the number of files sitting in the tenant's
  ingest folder that have not yet been picked up as jobs. `GET /api/v1/stats` gained `pending_ingest`
  (counts non-hidden files in `ingest/`, the same filter the worker uses to claim them).
- M6 RAG chat with citations: `POST /api/v1/chat {question, limit?}` -> grounded answer + citations,
  built by `DefaultRagAnswerer` over the M4 hybrid retriever + the default chat model
  (`DOKTOK_DEFAULT_MODEL`). Answers only from retrieved excerpts, cites them as `[n]`, and refuses
  ("I could not find enough evidence...") when retrieval is insufficient or the model declines.
  Document text is treated as untrusted data, not instructions. New **Chat** UI tab (answer + sources
  that open the cited document). `SearchHit` now carries the full chunk text for RAG context.
- Multilingual lexical term extraction: each document's language is detected (langdetect) and its
  significant terms are extracted with PostgreSQL `to_tsvector(<language>, text)` (stopwords removed,
  stemmed), stored as `CUSTOM_TOKEN` keyword entities (with frequency + language). The Entities tab
  gains a type filter; the detected language is recorded in document metadata. Config:
  `DOKTOK_LEXICAL_TERMS_LIMIT`. Builds on the existing M4 PostgreSQL full-text search layer
  (tsvector/tsquery/`ts_rank`/GIN on `document_chunks`).

### Fixed
- **`chunk_embed` hang / reconciler stall**: the embedding model (`qwen3-embedding:0.6b`) used Ollama's
  default 5-minute keep-alive while the chat model is pinned for 30, so it was evicted first and then
  could not reload while the big chat model stayed resident — hanging the embed call (up to the 600 s
  timeout) and stalling the single-threaded reconciler. The embedding model is now pinned with its own
  `DOKTOK_EMBEDDING_KEEP_ALIVE` (default `30m`), so it stays warm and `chunk_embed` doesn't block.
- **Worker stalling under GPU memory thrash**: the OCR-quality judge was hardcoded to a second model
  (`qwen3:14b`), so with the pipeline/RAG on the 24 GB `qwen3.6:35b-a3b` it loaded two large models on
  a tight budget — Ollama evicted/reloaded the in-use model and the single-threaded reconciler blocked
  on the stalled call (appearing "stuck" until a restart). The judge now uses the **same** model and
  context as the configured Ollama pipeline, so the worker keeps one large model resident.
- **Ingestion ledger over-deletion**: deleting or re-ingesting a document used `delete_for_sha`, which
  purged jobs by content hash and so could wipe out *other* documents' jobs that happened to share a
  SHA. Replaced it with `delete_for_document` (scoped by `document_id`) across the
  `IngestionJobRepository` port and its adapters; the document delete and reingest paths now use it.
- OCR **repeat-loops** no longer poison enrichment. On sparse/stamp pages glm-ocr could loop a line
  hundreds of times (e.g. `1.0 JAN. 2025 / SSOS MAL …`) until the output cap, filling `content.md`
  with garbage — which the enrichment LLM then "described" as titles like *"Unusual Repeating Text in
  Document"* or *"Error"*. Added a `repeat_penalty` to the OCR call to break loops, plus a safety net
  that **collapses runaway repetition** (a short cycle of identical lines repeated many times) before
  the text is stored. Real tables (distinct rows) are unaffected. Re-ingest affected documents to get
  clean titles.
- OCR no longer **fails a whole document** when a single page hits the output cap. The earlier
  `done: false` guard raised an error, which failed ingestion for documents with a dense/garbled
  page; it now keeps the partial transcription and logs a warning (a truncated page beats a failed
  document). Also raised the per-page `num_predict` cap from 4096 to 8192 so fewer pages truncate.
- Enrichment was ignoring `think=false` because the extractors passed `think` inside `options`, where
  Ollama silently drops it — so the model kept thinking (~87 s/document). Moving `think` to the
  top-level request field cut enrichment to **~8–11 s/document** (~8–10× faster) with the model warm.

### Changed
- The feature reconciler now drains in **parallel** (`DOKTOK_RECONCILE_CONCURRENCY`, default 2): several
  workers each claim a distinct row (`FOR UPDATE SKIP LOCKED`) and process it concurrently, so a
  corpus-wide backfill (e.g. extracting transactions across every document) finishes proportionally
  faster. Bound it by Ollama/DB capacity; the worker DB pool is sized for both ingest + reconcile
  streams.
- The worker now runs **ingestion and feature reconciliation as independent parallel streams**
  (separate threads) instead of one sequential loop. A large reconciler backfill (e.g. extracting
  transactions across the whole corpus) no longer **starves new ingestion** — folder watching and the
  reconciler proceed concurrently, sharing only the thread-safe DB pool and the local Ollama server.
  (RAG is already a separate stream in the backend process.) The reconcile stream drains while work
  exists and backs off to a poll cadence when idle; the DB pool was widened to cover both streams.
- The **OCR-quality judge** now uses a dedicated `DOKTOK_JUDGE_MODEL` (default `qwen3:14b`) instead of
  `DOKTOK_DEFAULT_MODEL`. This keeps the whole ingestion path on the dense model, so it never loads the
  23 GB qwen3.6 mid-ingest and evicts the resident enrichment model on a ~48 GB box (RAG chat still uses
  `DOKTOK_DEFAULT_MODEL`). Smaller `judge_num_ctx=8192` since it only compares a page of text.
- **Enrichment now defaults to the dense `qwen3:14b` with `think=false`** (was the qwen3.6 MoE).
  Generation is fast (~27 tok/s); the key tuning is a small **4096 context** + `keep_alive` so the
  model loads quickly (~14 s once) and stays warm across a batch (a 16k context made each load ~50 s by
  reallocating a ~19 GB KV cache). The language instruction was strengthened so non-English documents
  still get a native-language title/summary. Set `DOKTOK_ENRICH_MODEL=qwen3.6:35b-a3b` +
  `DOKTOK_ENRICH_THINK=true` to trade speed for the MoE's higher quality/language fidelity. (Note:
  enrichment stays fast only while `qwen3:14b` is resident; running the qwen3.6 OCR-judge or RAG chat
  concurrently can evict it and reintroduce reloads on a ~48 GB box.)
- Default ingest concurrency lowered from 4 to **2** (`DOKTOK_INGEST_CONCURRENCY`). With OCR +
  embedding + the new enrichment models all going through one local Ollama, 4 parallel documents could
  thrash GPU memory and time out; 2 is comfortable on ~48 GB. (Several scanned-PDF ingests had failed
  with 600 s Ollama timeouts under the old 4-wide setting + the pre-fix 32k OCR context.)
- OCR (`glm-ocr`) now runs at a **bounded context** instead of the model default: `num_ctx=8192`
  (configurable via `DOKTOK_OCR_NUM_CTX`; raise to 16384 for very dense/multi-column pages), a
  `num_predict=4096` per-page output cap, and `keep_alive=5m` (OCR is bursty — not pinned like the chat
  model). A single page only needs ~4.4k tokens (image tiles + prompt + output), so the previous 32k
  context reserved ~1 GB of KV cache for nothing. The OCR call now also fails loudly on an incomplete
  (`done: false`) generation instead of silently returning truncated text.
- Default embedding model switched from `mxbai-embed-large` to **`qwen3-embedding:0.6b`** (still
  1024-dim, so no schema change) because mxbai truncates inputs at 512 tokens while DokTok's chunks can
  be larger. `ChunkEmbedFeature`'s version is bumped to 2, so the **feature reconciler automatically
  re-embeds the whole corpus** for each tenant (run the worker after upgrading; `ollama pull
  qwen3-embedding:0.6b`). Changing the embedding model always requires a re-index, which this version
  bump performs.
- Ollama HTTP timeouts are now generous (`DOKTOK_OLLAMA_TIMEOUT_SECONDS`, default 600) and applied to
  OCR, embedding, and chat calls. Under parallel ingestion (`DOKTOK_INGEST_CONCURRENCY` > 1) requests
  queue at Ollama, and the previous short timeouts (120-180s) caused jobs to fail with
  `internal_error` ("timed out"). To make Ollama run requests concurrently instead of queuing, start
  the server with `OLLAMA_NUM_PARALLEL` set.
- Consistent `docs.active/{id}/` structure: every active document now has a `normalized/` directory
  holding the canonical "system document" - `normalized/searchable.pdf` for scanned/OCR'd input, or a
  verbatim copy of the original (`normalized/original.<ext>`) when no normalization was needed (the
  root `original.<ext>` is still kept). `manifest.system_document` therefore always points into
  `normalized/`, and `manifest.json` now records the detected `language` (was hardcoded `unknown`).
- Scanned PDF pages that already have an embedded text layer are no longer blindly re-OCR'd: a clean
  layer is kept (text-quality fast-path), and for ambiguous pages the default LLM
  (`DOKTOK_DEFAULT_MODEL`) judges whether the embedded text or the fresh OCR is better and keeps the
  winner (deterministic `text_quality` heuristic as fallback). Adds the Ollama chat adapter and
  `DOKTOK_OCR_MIN_TEXT_QUALITY`.
- OCR page selection is now image-coverage based (`PdfClassifier.page_image_coverage` +
  `DOKTOK_OCR_IMAGE_COVERAGE`, default 0.8): a PDF page that is essentially a full-page image is
  re-OCR'd even if it carries an existing (weak) embedded text layer, which is dropped; born-digital
  text pages with small figures keep their embedded text.
- API routes are now versioned under `/api/v1` (e.g. `/api/v1/ingestion/jobs`); `/health` stays
  unversioned. Added a `developer` tenant token (`dev-token-developer`) for local manual testing.
- `docs.active/{id}/` layout: the original is stored with its real extension (`original.<ext>`,
  openable), `manifest.json` is structured and names the canonical `system_document`, and a
  `normalized/searchable.pdf` slot is reserved for the OCR-derived document (M3).
- OCR (M3) model is configurable via `DOKTOK_OCR_MODEL` (default `glm-ocr:latest`).
- M2 text/PDF extraction: born-digital `.txt`/`.md`/PDF (PyMuPDF) become active documents with
  canonical artifacts (`manifest.json`, `content.md`, `content.json`, `pages/`); tenant-scoped
  `documents` table (migration 0003) + repository; `/api/v1/documents` API; Documents UI; scanned
  PDFs/images flagged `needs_ocr`. The ingestion job now runs through to `active`.

### Added
- UI usability pass: an Overview dashboard (document/entity/job counts + recent activity), a document
  detail viewer (metadata + extracted text + entities + activity), live auto-refresh + manual Refresh
  on Ingestion/Documents/Activity, and cross-linking (search hit / entity / job -> open the document).
  New read endpoints: `GET /api/v1/documents/{id}/content`, `/api/v1/documents/{id}/entities`,
  and `GET /api/v1/stats`.
- M5 entity indexing: rule-based `RegexEntityExtractor` (EMAIL, URL, MONEY, DATE, INVOICE_ID,
  CONTRACT_ID), `document_entities` (migration 0006, tenant-scoped), `EntityRepository` (Postgres +
  in-memory) with distinct-listing and documents-for-entity, entity extraction during activation,
  `GET /api/v1/entities` + `/api/v1/entities/documents`, and an Entities UI tab. spaCy NER
  (PERSON/ORG/GPE) is a documented follow-up.
- M4 vector + full-text hybrid search: deterministic fixed-window `Chunker`, Ollama embeddings
  (`OllamaEmbeddingProvider`, mxbai-embed-large, 1024-dim), `document_chunks` (migration 0005) with a
  pgvector HNSW index and a generated `tsvector` GIN index, `ChunkRepository` (Postgres + in-memory),
  `HybridPostgresRetriever` (pgvector + Postgres FTS fused with Reciprocal Rank Fusion), indexing
  during activation (a document is not active until indexed), `GET /api/v1/search`, and a Search tab.
- Activity/audit log: an immutable, append-only, tenant-scoped trail of document activities
  (`audit_events`, migration 0004). The ingestion pipeline emits `document.received` /
  `.identified` / `.activated` (with a per-type summary, page count, OCR confidence) / `.failed`
  (with error code) / `.quarantined`, correlated by job and document. New `AuditEventType`
  vocabulary, `AuditLogRepository.record`/`list_events` (Postgres + in-memory), the read-only
  `GET /api/v1/audit` API (optional `document_id` filter), and an Activity tab in the UI.
- M3 OCR extraction: scanned PDFs and images are OCR'd via a local Ollama vision model
  (`OllamaVisionOcr`, `DOKTOK_OCR_MODEL`); a derived `normalized/searchable.pdf` (images + invisible
  OCR text layer, built with PyMuPDF) becomes the canonical `system_document`. Mixed PDFs keep
  embedded text and OCR only blank pages. OCR confidence is recorded. New ports `OcrExtractor`,
  `PdfRenderer`, `SearchablePdfBuilder` and the OCR-aware `extract_document` orchestration.
- Project kickoff: architecture proposal, six ADRs, and the M0-M10 milestone roadmap.
- Repository metadata, issue templates, and the tracked backlog (granular M0 tickets + M1-M10 epics).
- M0 skeleton: uv + pnpm monorepo with 12 workspace packages; contracts-first ports and schemas;
  core settings (`DOKTOK_*`) and DI registry skeleton; FastAPI backend with `GET /health`;
  React + Vite UI shell with a backend status panel; PostgreSQL 17 + pgvector via Docker Compose;
  Makefile, GitHub Actions CI, import-linter hexagonal enforcement, pre-commit, secrets baseline,
  and SBOM target.
- M1 folder ingestion: folder-watching worker with stable-file detection, atomic move into the
  document lifecycle, streaming SHA-256, content-based MIME detection (libmagic), default security
  policy (allowlist + size limit, quarantine, dedup by hash), a SQL migration runner with the
  `ingestion_jobs` table, a Postgres ingestion job repository (plus in-memory fake), the
  `GET /api/ingestion/jobs` API, a UI ingestion jobs list, and Postgres integration tests in CI.
- M1.5 multi-tenancy and token auth: `tenant_id` on every schema and on `ingestion_jobs`
  (migration 0002), tenant-scoped repositories (per-tenant dedup), per-tenant filesystem lifecycle
  folders, a multi-tenant worker, bearer-token authentication mapping tokens to tenants
  (constant-time, fail-closed, loopback default), tenant-scoped ingestion API, a token-injecting UI
  dev proxy, and ADR-0007/ADR-0008.
