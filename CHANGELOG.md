# Changelog

All notable changes to DokTok NG are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
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
