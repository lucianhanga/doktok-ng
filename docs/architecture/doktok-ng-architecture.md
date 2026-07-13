# DokTok NG — Architecture

Status: Proposed
Date: 2026-06-10

## 1. Purpose

DokTok NG is a slim, local-first, AI-enabled **document-intelligence** system. It ingests documents
from local folders, extracts text and structure, indexes them for hybrid search, supports RAG chat
with citations, and later exposes document knowledge through a read-only MCP server for clients such
as Claude Code, GitHub Copilot, and PersonalAI.

DokTok NG is **not** a generic AI assistant. The first product goal is reliable document ingestion and
indexing. Chat and MCP become primary only after that foundation is solid.

It reuses the architectural *style* of [personal-ai](https://github.com/lucianhanga/personal-ai)
(local-first, modular monolith, ports and adapters, contracts-first, FastAPI + TypeScript, PostgreSQL +
pgvector, Ollama, security-first), narrowed to documents. It does not copy PersonalAI's
generic-assistant features.

## 2. Quality attributes (what the design optimizes for)

- **Privacy by default** — no egress, no remote providers unless explicitly enabled.
- **Reliability of ingestion** — a document never becomes `active` until every indexing step succeeds.
- **Maintainability** — one developer plus coding agents; clear module boundaries.
- **Replaceability** — adapters behind ports so infrastructure can change without rewriting core.
- **Auditability** — sensitive operations are recorded.
- **Slimness** — boring proven tools; no premature infrastructure.

## 3. Architectural style

A **local-first modular monolith** using **ports and adapters (hexagonal)** with **contracts-first**
schemas.

- Core domain logic depends only on **ports** (interfaces).
- Infrastructure details live in **adapters**.
- `import-linter` enforces the dependency direction (core must not import adapters).
- The **worker** runs as a separate process from the backend but shares the same core packages.
- The **MCP server** is introduced later and is read-only first.

## 4. Runtime architecture

```
React UI (Vite)
   |
   v
FastAPI Backend  ----------------------------+
   |                                          |
   +--> Core document services               |
   |       ingestion orchestration           |
   |       extraction / chunking             |
   |       indexing / retrieval / RAG        |
   |                                          |
   +--> PostgreSQL + pgvector  <-------- Worker process
   |        (metadata, FTS, vectors,         (folder watcher +
   |         entities, audit)                 ingestion pipeline,
   |                                          shares core packages)
   +--> Local filesystem storage             |
   |        (document lifecycle folders) <----+
   |
   +--> Ollama (chat + embeddings)
   |
   +--> MCP server (later, read-only first)
```

## 5. Module map (ports and adapters)

### Core ports (`contracts/`)

Repositories: `DocumentRepository`, `DocumentVersionRepository`, `IngestionJobRepository`,
`DocumentArtifactRepository`, `AuditLogRepository`.

File/IO: `FileStorage`, `MimeDetector`, `HashService`.

Extraction: `TextExtractor`, `DocumentNormalizer`, `PdfClassifier`, `PdfTextExtractor`,
`OcrExtractor`, `ImageExtractor`, `MarkdownExtractor`.

Indexing/AI: `Chunker`, `EmbeddingProvider`, `ChatModelProvider`, `EntityExtractor`, `Retriever`,
`RagAnswerer`.

Security: `SecurityPolicy`, `QuarantineService`.

### Adapters (by package)

- `storage/postgres` — `PostgresDocumentRepository`, `PostgresIngestionJobRepository`, ... + migrations.
- `storage/filesystem` — `LocalFileStorage`.
- `modalities/files` — `LibmagicMimeDetector`, `GotenbergNormalizer` (office -> PDF), and file-type handling.
- `providers/ollama` — `OllamaEmbeddingProvider`, `OllamaChatModelProvider`.
- extraction adapters — `PyMuPdfTextExtractor`, `DoclingExtractor`, `OcrMyPdfExtractor`, `SpacyEntityExtractor`.
- `retrieval/hybrid` — `HybridPostgresRetriever`.
- `tools/builtin`, `tools/mcp` — tool surfaces.

## 6. Storage spine — PostgreSQL + pgvector

A single PostgreSQL is the first storage spine (ADR-0002): relational metadata, JSONB extraction
artifacts, PostgreSQL full-text search, pgvector embeddings, normalized entity tables, and audit
tables. One database to operate, strong transactional guarantees, hybrid search without extra
infrastructure.

Initial tables (see brief §16): `documents`, `document_versions`, `ingestion_jobs`, `document_pages`,
`document_chunks` (with `embedding vector` + `tsv tsvector`), `document_entities`, `document_artifacts`,
`audit_events`. Later milestones add `document_features` (ADR-0009), `categories` +
`document_category_links` (M6.2), `extracted_records` (M6.3), and the Insights projection cache +
queue (`embedding_projections`, `embedding_projection_points`, `projection_requests`; §18, M7.1).
Migration `0030` (M8.x, #312) deletes the dropped low-value `document_entities` rows (§11).

## 7. Filesystem document lifecycle

```
storage/files/{tenant_id}/
  ingest/        user drops files here
  in.process/    worker moves files here while processing ({job_id}/source)
  docs.active/   only fully indexed documents ({document_id}/...)
  docs.failed/   failed processing jobs ({job_id}/...)
  quarantine/    suspicious or disallowed files
```

The lifecycle is rooted **per tenant** (ADR-0007): each tenant has its own ingest/in.process/...
folders so a dropped file's owner is unambiguous.

A successful document produces canonical artifacts under `docs.active/{document_id}/`:

```
docs.active/{document_id}/
  original.<ext>          original file, kept byte-for-byte with its real extension (e.g. original.docx)
  manifest.json           metadata + which artifact is the canonical "system document"
  content.md              canonical extracted text (plain UTF-8; chunked/embedded in M4)
  content.json            structured extraction (pages, method)
  pages/page-NNNN.json    per-page structured text
  thumbnails/thumb.webp   first-page preview, rendered from the system document
  normalized/
    searchable.pdf        derived OCR'd / converted PDF (images + text layer); the canonical viewable form
```

The **system document** (named in `manifest.json`) is the canonical **viewable** representation:

- **Scanned PDFs and images** -> the OCR'd `normalized/searchable.pdf` (page images + invisible text
  layer), produced in M3.
- **Office documents** (`.docx`/`.xlsx`/`.pptx`) -> the Gotenberg-converted PDF, written to
  `normalized/searchable.pdf` (M8.x, §20). If that converted PDF itself needs OCR, the searchable PDF
  carrying the recovered text layer is stored instead.
- **Born-digital PDFs** that need no normalization -> a verbatim copy of the original under
  `normalized/` (kept for a consistent structure), and the original PDF is the viewable form.

The root `original.<ext>` is always preserved byte-for-byte as the openable source of record, and is
what **Download** returns. Thumbnails, page images, and the OCR text-region overlay all derive from
the system document, so they work uniformly across PDFs, images, and office documents. Not every
document has every artifact.

## 8. Ingestion pipeline and state machine

Folder-based ingestion plus database-backed job state (ADR-0004). The worker waits for file stability,
atomically moves the file to `in.process`, computes SHA-256, detects MIME by content, validates against
the security policy, routes by file type, extracts, chunks, embeds, indexes (vectors + FTS), extracts
entities, writes audit events, and only then marks the document `active`.

**Office documents** (`.docx`/`.xlsx`/`.pptx`) take one extra step in the `normalizing` stage: the
extraction service routes their OOXML MIME types through the `DocumentNormalizer` port, which converts
them to a PDF via a local Gotenberg container (§20). The converted PDF then reuses the entire existing
PDF extraction / render / OCR / thumbnail path, so office support adds no parallel pipeline. The
converted PDF becomes the document's system document; the original `.docx`/`.xlsx`/`.pptx` is still
preserved verbatim. If the normalizer is unreachable, office files fail with `needs_ocr`.

Job states:

```
queued -> detecting -> hashing -> normalizing -> extracting -> chunking ->
embedding -> indexing -> activating -> active
                                       \-> failed
                                       \-> quarantined
```

A document must not become `active` until extraction, canonical artifacts, chunks, embeddings, FTS
indexes, entities, and the audit event are all complete.

## 9. Search and retrieval

**Hybrid retrieval from the first search milestone** (ADR-0005), never vector-only. Signals:
pgvector semantic search, PostgreSQL full-text search, and entity/token search; reranking later.
Search results carry document id, title/filename, chunk id, page number, snippet, score components,
extraction method, and citation metadata.

## 10. RAG chat

RAG answers must include citations (document id, title/filename, page, chunk id, extraction method,
OCR confidence where relevant). The answerer must be able to say it could not find enough evidence,
rather than producing ungrounded answers.

### OCR routing (M3)

OCR runs on the configurable engine `DOKTOK_OCR_ENGINE` — default `paddleocr` (PP-OCRv5: a
deterministic, CPU-only detection+recognition pipeline with native per-line confidence; ADR-0010); the
legacy local Ollama vision model `glm-ocr` remains selectable (any value other than `paddleocr` routes
to the vision adapter). A PDF page is OCR'd when it has no
embedded text **or** its largest image covers at least `DOKTOK_OCR_IMAGE_COVERAGE` of the page (a
full-page scan). In the latter case any existing embedded text layer is **dropped and re-OCR'd**, so
pages OCR'd by a weaker engine are redone. Born-digital pages (real text, only small figures) keep
their embedded text; mixed PDFs combine both per page. Fully-OCR'd documents also get a derived
`normalized/searchable.pdf` (page images + an invisible OCR text layer) as the system document.

PaddleOCR is GIL-serialized, so the worker runs it in a `ProcessPoolExecutor` of
`DOKTOK_OCR_CONCURRENCY` processes (each ~1 core, ~1-1.5 GB RAM) for real cross-page parallelism;
`DOKTOK_OCR_CPU_THREADS` caps math-library threads per process (keep `concurrency * cpu_threads <=
cores`). The pool resizes live from Settings between ingest scans. Files dropped in `ingest.enhanced/`
use a heavier PP-OCRv6 medium profile with a 4-way orientation vote. CPU acceleration via oneDNN
(`DOKTOK_OCR_ENABLE_MKLDNN`, default on) **must be disabled on Intel N95 / Alder Lake-N**, where
PaddlePaddle's oneDNN kernels crash (ADR-0010).

OCR is moving toward a **pluggable, device-aware** model (ADR-0021): a host probe drives
`GET /api/v1/settings/ocr/recommendation` (`{engine, concurrency, reason}`, shown as a Settings hint —
shipped, M17), and a RapidOCR (ONNX/OpenVINO) adapter plus live in-UI engine selection are planned.
PaddleOCR remains the default until RapidOCR is benchmarked on the N95.

## 11. Entity extraction

Entities come from three independent extractors composed behind their ports:

- **Rule-based regex** (`RegexEntityExtractor`) — emits **EMAIL** and **URL** only. The low-value
  types (MONEY, DATE, INVOICE_ID, CONTRACT_ID, DOCUMENT_ID) were dropped (M8.x, #312): their regex
  matches were ~90% noise on real documents, monetary data lives in extracted records, and dates live
  in document metadata. Those enum values remain in the `EntityType` vocabulary for back-compat so
  historical rows still resolve, but nothing emits them; migration `0030` deletes existing rows of the
  five dropped types.
- **NER** (`EntityNerExtractor`, M7.4) — **PERSON / ORG / GPE**.
- **Lexical** (`LexicalTermExtractor`, M5.7) — **CUSTOM_TOKEN** keyword terms in the document's
  detected language.

**Pluggable enrichment backends (ADR-0023).** NER and KAG relation extraction run behind their ports,
so each can use either the configured pipeline LLM or a local span model:

- **NER** — remote OpenAI `gpt-4o-mini` (most accurate, default) or local **GLiNER** (`gliner_large-v2.5`,
  ~9× faster, no egress).
- **Relations** (`RelationExtractor`, KAG) — local **GLiNER-Relex** (`gliner-relex-large-v1.0`) is the
  recommended default (best F1 + faster + local on the benchmark corpus); remote OpenAI is the alternative.

Both local backends live in `doktok-provider-gliner` (heavy deps opt-in via `make ner-models`) and are
selected per-purpose in **Settings → AI** (NER and KEG are their own AI purposes, alongside the
general `pipeline`, `rag`, and `embedding`); they need no egress. Benchmarks and the local/remote
decision: [ADR-0023](../adr/ADR-0023-pluggable-ner-and-relation-backends.md), reproducible with
`make ner-bench` / `make kg-bench`.

**Knowledge-graph entity model.** How mentions become cross-document nodes, and every special case
(one-node-per-person with name parts as attributes, token-sorted identity, reversible merges,
suggestions-never-auto-merge, postal-code/city split, the shared-surname *possible family* hint) is
documented in [knowledge-graph-entities.md](knowledge-graph-entities.md). Read it before changing
entity identity, merges, or relation edges.

Later: domain dictionaries, richer normalization, a Settings-UI backend selector.

## 12. Security model

Local-first, no-egress-by-default (ADR-0006). All files/text/model/tool/MCP I/O are untrusted.
Controls: MIME allowlist, max file size, max page count, quarantine folder, no execution of document
content, audit log, read-only MCP first, explicit permissions for any future write tools or remote
providers.

## 13. MCP strategy

Introduced after ingestion, search, and RAG work (M8). Read-only first. Initial tools:
`doktok.search_documents`, `doktok.get_document`, `doktok.get_chunk`, `doktok.ask_documents`,
`doktok.list_entities`, `doktok.find_related_documents`, `doktok.get_ingestion_status`. No arbitrary
SQL, no arbitrary filesystem access; all MCP access audited.

## 14. Technology stack

- Backend: Python 3.12, FastAPI, Pydantic, `uv` workspace, pytest, ruff, mypy.
- Frontend: TypeScript, React, Vite, `pnpm`, Vitest.
- Database: PostgreSQL 17, pgvector, migrations (Alembic or equivalent).
- AI runtime: Ollama by default (default chat `qwen3.6:35b-a3b`, default embedding
  `qwen3-embedding:0.6b`); OpenAI is an opt-in remote provider selectable per purpose (see §17).
- File processing: content-based MIME detection (libmagic/python-magic), PyMuPDF, PaddleOCR; office
  (OOXML) -> PDF conversion via a local Gotenberg container (§20).
- Deployment: Docker Compose for local dev (Postgres + Gotenberg); no Kubernetes in the first phase.

## 15. What we deliberately avoid early

No microservices, no Kubernetes, no Redis, no Elasticsearch, no Qdrant, no MinIO, no graph database.
Adapters allow adding any of these later without rewriting core. Do not overbuild.

## 15a. Multi-tenancy and authentication

DokTok NG is multi-tenant from the foundation (ADR-0007, ADR-0008).

- **Isolation:** a single shared PostgreSQL database with a `tenant_id` discriminator on every
  tenant-owned table. All repository reads are scoped by `tenant_id`; deduplication is per tenant.
- **Filesystem:** the document lifecycle is rooted per tenant at `storage/files/{tenant_id}/...`.
- **Authentication:** clients send `Authorization: Bearer <token>`; the backend resolves the token to
  a tenant (constant-time compare) and scopes the request. `/health` is public; `/api/*` requires a
  token. The server binds loopback by default and fails closed when no tokens are configured.
- **Token store:** a static `DOKTOK_TENANT_TOKENS` JSON map (`{"<token>": "<tenant_id>"}`) now;
  DB-backed `tenants` + `api_tokens` (hashed, revocable) later behind the same interface.

Tenant identity always comes from the authenticated token, never from request input. Every future
milestone (extraction, search, RAG, MCP) inherits this scoping.

## 16. Document features, thumbnails, and the library/pipeline split

Beyond the blocking extraction stage, additive capabilities run as **reconciled features** (ADR-0009):
each is a versioned, idempotent `FeatureProcessor` listed in the feature catalog
(`core/.../features/catalog.py`), driven to `done` per `(tenant, document, feature)` by the worker
reconciler. The current catalog: `chunk_embed` (RAG index), `entities` (entities + keyword tokens),
`doc_metadata` (title / date / location / summary), `doc_classify` (categories), `structured_records`
(typed line items), and `thumbnail`.

**First-pages enrichment (M8.x, #311).** The two cheap LLM features read only the **opening pages** of
a document, not its full text, because title/summary/date/location and the document category are
well-determined by the first pages — cutting tokens, latency, and cost. A page-aware helper
(`_head_pages` in `features/processors.py`) feeds `doc_metadata` the first ~2 pages (capped at ~6k
chars) and `doc_classify` the first ~3 pages (capped at ~8k chars); the budgets are in-code constants.
The heavier features (`chunk_embed`, the regex/NER/lexical `entities`, and `structured_records`) still
read the whole document. Feature versions were intentionally **not** bumped, so this applies to newly
ingested or reprocessed documents rather than triggering a corpus-wide re-run.

The **`thumbnail`** feature renders the first page of a document's normalized PDF to a small WebP via
the `PyMuPdfThumbnailer` adapter (`modalities/files/.../render.py`; `fitz` rasterize → Pillow Lanczos
downscale → WebP, both imported lazily). It writes `docs.active/<id>/thumbnails/thumb.webp`, served by
`GET /api/v1/documents/{id}/thumbnail` (404 → UI placeholder until rendered, or for unrenderable
documents). Because it is a reconciled feature, the whole corpus backfills automatically and a version
bump re-renders it.

Because a document is `active` as soon as it has extracted content (features fill in afterwards), the
**Overview** dashboard separates two concerns:

- the document **library** — Documents / Entities / Categories counts (steady-state inventory);
- the **Ingestion** pipeline — only actionable states (Waiting in ingest / Processing / Failed /
  Pending features), or "Pipeline idle" when nothing is in flight.

The raw job count is deliberately not shown (an `active` job only duplicates the document count); in
the Ingestion view a finished job's `active` status is relabelled "ingested" so the word "active" only
ever describes a document.

## 17. Document-list query model and runtime AI selection

### Document-list querying

`GET /api/v1/documents` is **keyset-paginated**, not offset-paginated: it returns an opaque cursor that
encodes the active sort key, direction, the last row's value, and its id. NULLs sort last; a stale or
mismatched cursor (e.g. a different sort) is rejected with `400` rather than silently mis-paging.
Supported `sort` keys: `acquired` (ingestion `created_at`, default), `created` (the document's own
`document_date`), `title`, `category`; with `dir=asc|desc`. Filters: `status`, `category`,
`needs_attention`, and **token** filters (`token[]` with `token_match=all` (AND, default) `|any` (OR)
and optional `token_type`). `GET /api/v1/documents/ids` returns every id matching the same filters
(capped at 10k with a `truncated` flag) so "select all matching" acts on the full result set, not just
the loaded page. Per-sort composite keyset indexes back these queries (migrations 0016, 0018); the
`DocumentRepository` port carries `list_documents` (extended) and `list_document_ids`, with the
`DocumentSort` / `SortDir` / `TokenMatch` / `ListAnchor` contracts. The Documents tab renders this as
interchangeable **List** (table) and **Thumbnails** (gallery) views over one toolbar and one
selection model. See ADR-0013.

### Runtime AI model selection

Model choice is configurable at runtime via the **Settings** tab, not only by environment variables.
Global system settings (not tenant-scoped) persist in an `app_settings` key→JSON table (migration
0017) and are merged over the env defaults at startup (changes apply on restart). The Settings UI lets
the operator pick a model **per purpose** — the ingestion pipeline (feature extraction) and RAG /
interrogation — from a catalog (`core/.../settings/catalog.py`) spanning local Ollama and remote
OpenAI, with a unified reasoning-density control (`off|low|medium|high`) mapped to each provider's own
knob. The OpenAI API key is **write-only** (set/cleared, never returned). Selecting an OpenAI model is
an explicit, opt-in exception to the local-first / no-egress default (§12, ADR-0006); the defaults are
Ollama-only. Adapters live in `providers/openai`. See ADR-0014.

## 18. Insights: embedding-space visualization (M7.1, ADR-0016)

The **Insights** tab visualizes the RAG embedding space. Its first sub-tab, **Embedding Space**,
projects every chunk embedding (1024-dim) down to 2D/3D and plots it colored by the chunk's document
**primary category**, so clusters (topics), category separation, and outliers become visible.

This introduces the system's **first tenant-aggregate background job** — distinct from the
per-document `FeatureReconciler` (ADR-0009). A projection fits *all* of a tenant's chunk embeddings
jointly (UMAP preferred, PCA fallback; 2D and 3D are independent fits), so it cannot be a per-document
`FeatureProcessor`. The flow, with no message broker:

- **Cache** (`embedding_projections` + `embedding_projection_points`, migration 0019): one cached
  projection per `(tenant, dim)`, holding the points plus an `input_fingerprint` (chunk count +
  newest row + algorithm + version). Points reference the projection header, not `document_chunks`,
  so a snapshot survives re-embedding until an explicit recompute; staleness is detected by comparing
  fingerprints, never by row mutation.
- **Queue** (`projection_requests`, migration 0020): the recompute button enqueues one row per tenant
  (`UNIQUE`, so repeated presses coalesce). The worker runs a **separate projection stream** that
  claims a request (`FOR UPDATE SKIP LOCKED`), fits 2D + 3D, writes the cache, and clears the request.
  UMAP is CPU-heavy, so this stream is independent of ingestion and reconciliation.
- **Read API** (tenant-scoped): `GET /api/v1/visualizations/embeddings?dim=2|3` returns points
  (`x,y,z?`, category, document, snippet) + a **server-owned** category→color legend + projection
  meta (incl. `stale`); `GET .../status` reports per-dim cache state + whether a recompute is in
  flight; `POST .../recompute` enqueues one. Color uses each document's primary category (the linked
  category with the highest tenant-wide document count; documents are multi-label, ADR-0016 §3), so
  the server owns the palette and the 2D view, 3D view, and legend always agree.
- **UI**: the SVG scatter (2D direct, 3D rotatable) with legend show/hide, hover tooltip, click-to-
  open-document, and all API-driven states (loading/empty/not-computed/stale/truncated/error). The
  renderer is dependency-free SVG at current scale; the API contract is stable so a WebGL renderer
  (deck.gl) can replace it for very large tenants without backend changes.

The reducer adapter (`providers/projection`, `SklearnUmapReducer`) keeps `umap-learn`/`scikit-learn`/
`numpy` as an optional, lazily-imported `engine` extra (like PaddleOCR), installed with
`make projection-engine`. `uv sync` prunes it, so re-run that on a worker host after a sync.

## 19. Office-document support via local Gotenberg (M8.x, #313, ADR-0019)

DokTok NG ingests Microsoft Office **OOXML** documents — `.docx`, `.xlsx`, `.pptx` — alongside PDFs
and images. They are added to the security allowlist (by content-detected MIME, not extension) and
converted to PDF **on ingest** so they reuse the entire canonical PDF path (extract / render / OCR /
thumbnail / preview) with no parallel pipeline.

- **Port + adapter.** A new `DocumentNormalizer` port (`contracts/.../ports.py`) declares
  `to_pdf(path, mime) -> bytes`. The `GotenbergNormalizer` adapter
  (`modalities/files/.../normalize.py`) POSTs the file to Gotenberg's
  `/forms/libreoffice/convert` route and returns the PDF bytes.
- **Routing.** The extraction service (`core/.../extraction/service.py`) recognizes the three OOXML
  MIME types, calls the normalizer to get a PDF, then runs the existing `_extract_pdf` path on it. The
  converted PDF is persisted as the normalized/system document (the searchable PDF wins if the
  converted PDF itself needed OCR). Wiring is in `apps/worker/.../composition.py` and
  `core/.../ingestion/pipeline.py`; the normalizer is optional, and when absent office files fail with
  `needs_ocr`.
- **Engine.** [Gotenberg](https://gotenberg.dev) (`gotenberg/gotenberg:8`) wraps headless LibreOffice,
  is **MIT-licensed**, and is published as an official Docker image. It runs as a service in the local
  compose stack, so **document content never leaves the host** (no egress; consistent with ADR-0006).
  It is the single added engine, chosen for a clean supply chain and fully local conversion.
- **Settings.** `DOKTOK_GOTENBERG_URL` (default `http://localhost:3000`) points the worker at the
  container; `DOKTOK_GOTENBERG_PORT` (default `3000`) overrides the compose host port to avoid clashes.

**Preview / download behavior.** The in-browser preview shows an office document inline via its
normalized PDF, exactly like a native PDF; "Open in new tab" opens that PDF; **Download** returns the
**original** file (e.g. the `.docx`), not the PDF. Thumbnails, page images, and the OCR text-region
overlay all derive from the system document, so they behave uniformly for office documents.

## 20. Roadmap

See [../milestones/M0-M10.md](../milestones/M0-M10.md). Every milestone ships a runnable system; one
milestone per pass. The blocking ingestion foundation (M0–M3), search (M4), entities (M5), RAG chat
(M6), enrichment/aggregation (M6.1–M6.3), the Insights embedding map (M7.1), and the M8.x pipeline
cost/format work (first-pages enrichment, entity cleanup, office-document support) are implemented;
remaining work is M8 (read-only MCP), M9 (advanced document tools), and M10 (external integrations).
