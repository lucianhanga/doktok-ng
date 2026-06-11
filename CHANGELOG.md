# Changelog

All notable changes to DokTok NG are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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

### Changed
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
