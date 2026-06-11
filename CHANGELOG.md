# Changelog

All notable changes to DokTok NG are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
