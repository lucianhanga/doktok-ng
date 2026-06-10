# DokTok2 Full Claude Code Brief

Use this document as the single handoff prompt/specification for Claude Code.

---

# 1. Project Goal

Build **DokTok2** from scratch as a slim, local-first, AI-enabled document management system.

DokTok2 should ingest documents from local folders, extract text and structure, index them, make them searchable, support RAG chat with citations, and later expose the document knowledge through an MCP server for systems such as:

- Claude Code
- GitHub Copilot
- PersonalAI
- other MCP-compatible clients

DokTok2 is inspired by the public repository:

```text
https://github.com/lucianhanga/personal-ai
```

Important: do **not** blindly copy PersonalAI. Reuse its architectural style, but keep DokTok2 narrower and document-focused.

---

# 2. Product Definition

DokTok2 is **not** a generic AI assistant.

It is a **document intelligence system**.

It should feel like:

```text
Paperless-ngx + local RAG + MCP + entity search
```

The first product goal is not “chat with files”. The first product goal is reliable document ingestion and indexing.

The system should be able to answer operational questions such as:

- What file was ingested?
- What type of file is it?
- Was it processed successfully?
- What text was extracted?
- Which chunks were indexed?
- Which entities were found?
- Can I search it?
- Can I cite the source?

Only after this foundation is reliable should chat and MCP become the main focus.

---

# 3. Architectural Inspiration from PersonalAI

Study:

```text
https://github.com/lucianhanga/personal-ai
```

Reuse the following architectural ideas:

- local-first runtime
- modular monolith
- ports and adapters
- contracts-first schemas
- FastAPI backend
- TypeScript frontend
- Python `uv` workspace
- `pnpm` frontend workspace
- PostgreSQL + pgvector
- Ollama model provider
- RAG with citations
- security-first file/tool/MCP boundaries
- auditability
- milestone-based delivery

Do not reuse unnecessary generic-assistant features unless they directly serve DokTok2.

DokTok2 should specialize PersonalAI’s architecture around:

- file ingestion
- document lifecycle
- extraction
- OCR
- chunking
- indexing
- entity extraction
- hybrid retrieval
- RAG over documents
- MCP access to document knowledge

---

# 4. Core Product Principles

1. Local-first.
2. No remote AI provider by default.
3. No network egress by default.
4. PostgreSQL is the first storage spine.
5. Use pgvector inside PostgreSQL for embeddings.
6. Use PostgreSQL full-text search for exact and lexical search.
7. Use entity/token indexing for names, IDs, dates, and domain terms.
8. Use hybrid retrieval, not vector-only retrieval.
9. Treat all files and extracted content as untrusted.
10. Avoid microservices at the beginning.
11. Avoid premature Kubernetes, MinIO, Redis, Elasticsearch, Qdrant, or graph databases.
12. Use adapters so components can be replaced later.
13. Every milestone must produce a functional system.
14. Keep the system maintainable by one developer plus coding agents.

---

# 5. Default Models

Use Ollama as the default local model runtime.

```env
DOKTOK_DEFAULT_MODEL=qwen3.6:35b-a3b
DOKTOK_EMBEDDING_MODEL=mxbai-embed-large:latest
DOKTOK_OLLAMA_BASE_URL=http://localhost:11434
```

Both model names must be configurable through environment variables.

The default generic local LLM is:

```text
qwen3.6:35b-a3b
```

The default embedding model is:

```text
mxbai-embed-large:latest
```

Remote providers may be added later, but must be disabled by default.

---

# 6. Recommended Technology Stack

## Backend

- Python 3.12
- FastAPI
- Pydantic
- `uv` Python workspace
- pytest
- ruff
- mypy

## Frontend

- TypeScript
- React
- Vite
- `pnpm`
- Vitest
- typecheck/lint scripts

## Database

- PostgreSQL 17
- pgvector
- migrations with Alembic or equivalent

## AI Runtime

- Ollama
- default chat model: `qwen3.6:35b-a3b`
- default embedding model: `mxbai-embed-large:latest`

## File/Document Processing

- file type detection through content, not extension
- `libmagic` / `python-magic` adapter
- PyMuPDF and/or Docling for born-digital PDF extraction
- OCRmyPDF/Tesseract and/or Docling OCR path for scanned PDFs and images
- direct text/Markdown parsing

## Deployment

- Docker Compose for local development
- no Kubernetes in the first phase

---

# 7. Required High-Level Repository Shape

```text
doktok2/
  contracts/
    README.md
    schemas/
    api/

  core/
    doktok_core/
      ingestion/
      documents/
      extraction/
      indexing/
      retrieval/
      entities/
      security/
      audit/

  apps/
    backend/
      doktok_api/
      tests/

    ui/
      src/
      tests/

    worker/
      doktok_worker/
      tests/

    mcp/
      doktok_mcp/
      tests/

  providers/
    ollama/
      doktok_provider_ollama/

  storage/
    postgres/
      doktok_storage_postgres/
      migrations/

    filesystem/
      doktok_storage_filesystem/

  modalities/
    files/
      doktok_modalities_files/

  retrieval/
    hybrid/
      doktok_retrieval_hybrid/

  tools/
    builtin/
      doktok_tools_builtin/

    mcp/
      doktok_tools_mcp/

  docs/
    architecture/
    adr/
    milestones/
    prompts/

  docker-compose.yml
  pyproject.toml
  package.json
  pnpm-workspace.yaml
  README.md
```

---

# 8. Runtime Architecture

```text
React UI
   |
   v
FastAPI Backend
   |
   +--> Core document services
   |       |
   |       +--> ingestion orchestration
   |       +--> extraction
   |       +--> chunking
   |       +--> indexing
   |       +--> retrieval
   |       +--> RAG chat
   |
   +--> PostgreSQL + pgvector
   |
   +--> local filesystem storage
   |
   +--> Ollama
   |
   +--> MCP server, later
```

The worker should run separately from the backend process, but share the same core packages.

The MCP server should be introduced later and should be read-only first.

---

# 9. Ports and Adapters

Core logic should depend on interfaces, not infrastructure libraries.

Recommended core ports:

```text
DocumentRepository
DocumentVersionRepository
IngestionJobRepository
DocumentArtifactRepository
AuditLogRepository

FileStorage
MimeDetector
HashService

TextExtractor
PdfClassifier
PdfTextExtractor
OcrExtractor
ImageExtractor
MarkdownExtractor

Chunker
EmbeddingProvider
ChatModelProvider
EntityExtractor
Retriever
RagAnswerer

SecurityPolicy
QuarantineService
```

Adapters should implement:

```text
PostgresDocumentRepository
PostgresIngestionJobRepository
LocalFileStorage
LibmagicMimeDetector
OllamaEmbeddingProvider
OllamaChatModelProvider
PyMuPdfTextExtractor
DoclingExtractor
OcrMyPdfExtractor
SpacyEntityExtractor
HybridPostgresRetriever
```

---

# 10. Filesystem Layout

Use this document lifecycle layout:

```text
storage/files/
  ingest/
  in.process/
  docs.active/
  docs.failed/
  quarantine/
```

## Meaning

### `ingest/`

User drops files here.

### `in.process/`

Worker moves files here while processing.

Use:

```text
storage/files/in.process/{job_id}/source
```

### `docs.active/`

Only successfully extracted, chunked, embedded, indexed, and entity-indexed documents go here.

Use:

```text
storage/files/docs.active/{document_id}/
```

### `docs.failed/`

Failed processing jobs go here.

Use:

```text
storage/files/docs.failed/{job_id}/
```

### `quarantine/`

Suspicious or disallowed files go here.

---

# 11. Ingestion Pipeline

## Required Flow

1. User drops one or more files into:

```text
storage/files/ingest
```

2. Worker detects files.

3. Worker waits until the file is stable.

A file is stable when its size and modification timestamp have not changed for a configurable interval.

4. Worker atomically moves file to:

```text
storage/files/in.process/{job_id}/source
```

5. Worker computes SHA-256.

6. Worker creates an ingestion job in PostgreSQL.

7. Worker detects file type by content, not extension.

Use a `MimeDetector` port with a `libmagic/python-magic` adapter.

8. Worker validates file against security policy:

- MIME allowlist
- size limit
- page limit where applicable
- archive/compound file restrictions later
- no execution

9. Unsupported or suspicious files go to:

```text
docs.failed
```

or:

```text
quarantine
```

10. Worker routes processing by file type.

11. Worker creates canonical artifacts.

12. Worker chunks extracted content.

13. Worker creates embeddings using Ollama.

14. Worker stores vectors in PostgreSQL/pgvector.

15. Worker stores full-text search vectors.

16. Worker extracts named entities and important tokens.

17. Worker stores entities.

18. Worker writes audit events.

19. Only after all indexing succeeds, worker marks document `active`.

20. On failure, worker stores detailed error and moves artifacts to `docs.failed`.

---

# 12. Ingestion State Machine

Recommended job states:

```text
queued
detecting
hashing
normalizing
extracting
chunking
embedding
indexing
activating
active
failed
quarantined
```

A document must not become active until:

- extraction completed
- canonical artifacts were written
- chunks were created
- embeddings were stored
- full-text indexes were updated
- entities were extracted and stored
- audit event was recorded

---

# 13. File Processing Rules

## Text files

For `text/plain`:

- parse directly
- preserve original text as canonical content
- do not convert to PDF before indexing

## Markdown files

For `text/markdown`:

- parse directly
- preserve Markdown as canonical content
- preserve headings and structure where possible
- do not convert to PDF before indexing

PDF preview generation may be added later, but it is not canonical.

## Born-digital PDFs

For born-digital PDFs:

- extract embedded text
- preserve page metadata
- preserve page numbers
- preserve layout hints where possible
- do not OCR unless the embedded text is bad or incomplete

## Scanned PDFs

For scanned PDFs:

- OCR pages
- preserve OCR confidence
- create text artifacts
- optionally create searchable PDF artifact

## Mixed PDFs

For mixed PDFs:

- do not blindly destroy good embedded text
- extract embedded text where valid
- OCR only pages or regions that need OCR
- preserve page-level extraction method

## Images

For images:

- OCR directly
- preserve OCR confidence
- optionally create searchable PDF artifact
- store image metadata

---

# 14. Canonical Artifacts

A successfully processed document should produce a directory like:

```text
storage/files/docs.active/{document_id}/
  original
  manifest.json
  content.md
  content.json
  pages/
    page-0001.json
    page-0002.json
  normalized/
    searchable.pdf
```

Not every document will have every artifact.

## `manifest.json`

Should include:

```json
{
  "document_id": "...",
  "version_id": "...",
  "sha256": "...",
  "original_filename": "...",
  "detected_mime": "...",
  "detector": "...",
  "created_at": "...",
  "extraction_method": "...",
  "page_count": 0,
  "language": "unknown",
  "artifacts": []
}
```

## `content.md`

Canonical human-readable extracted content.

## `content.json`

Canonical structured extraction result.

Should include pages, headings, tables where available, extraction method, confidence, and metadata.

---

# 15. Chunking

Chunks should be deterministic and reproducible.

Each chunk should preserve:

- document id
- version id
- page range
- heading path if available
- source offsets if available
- extraction method
- OCR confidence where available
- token count
- text
- metadata

Chunking should avoid losing citation information.

Recommended chunk metadata:

```json
{
  "document_id": "...",
  "version_id": "...",
  "chunk_id": "...",
  "page_start": 1,
  "page_end": 2,
  "heading_path": ["Chapter", "Section"],
  "source_offsets": {
    "start": 0,
    "end": 1000
  },
  "extraction_method": "pdf_text",
  "ocr_confidence": null,
  "token_count": 250
}
```

---

# 16. Database Model

Create initial schemas for:

## `documents`

- id
- current_version_id
- sha256
- original_filename
- detected_mime
- title
- status
- storage_path
- created_at
- activated_at
- metadata jsonb

## `document_versions`

- id
- document_id
- version_number
- sha256
- created_at
- extraction_method
- manifest jsonb

## `ingestion_jobs`

- id
- document_id nullable
- source_path
- status
- detected_mime
- sha256
- error_code
- error_message
- started_at
- finished_at
- metadata jsonb

## `document_pages`

- id
- document_id
- version_id
- page_number
- text
- layout jsonb
- extraction_method
- ocr_confidence
- tsv tsvector

## `document_chunks`

- id
- document_id
- version_id
- page_start
- page_end
- heading_path
- text
- token_count
- embedding vector
- tsv tsvector
- metadata jsonb

## `document_entities`

- id
- document_id
- version_id
- chunk_id nullable
- entity_text
- entity_type
- normalized_value
- frequency
- metadata jsonb

## `document_artifacts`

- id
- document_id
- version_id
- artifact_type
- storage_path
- mime_type
- sha256
- created_at
- metadata jsonb

## `audit_events`

- id
- event_type
- actor
- document_id nullable
- job_id nullable
- timestamp
- metadata jsonb

---

# 17. Search and Retrieval

Implement hybrid retrieval from the first search milestone.

Do not implement vector-only retrieval as the main search path.

## Retrieval signals

1. Semantic vector search through pgvector.
2. PostgreSQL full-text search.
3. Entity and token search.
4. Later: reranking.

## Search result should include

- document id
- document title or filename
- chunk id
- page number if available
- snippet
- score components
- extraction method
- OCR confidence if relevant
- citation metadata

## Search filters

Support over time:

- document type
- filename
- created/imported date
- entity
- page range
- collection
- document status
- extraction method

---

# 18. RAG Chat with Documents

RAG answers must include citations.

A citation should include:

- document id
- document title or filename
- page number if available
- chunk id
- extraction method
- OCR confidence where relevant

The system should not confidently answer from documents if retrieval is insufficient.

The RAG answerer should be able to say:

```text
I could not find enough evidence in the indexed documents to answer that.
```

Avoid ungrounded answers.

---

# 19. Entity Extraction

Initial implementation can use:

- spaCy NER
- rule-based patterns
- regex for emails, URLs, dates, invoice numbers, contract numbers

Later implementation can add:

- LLM-assisted extraction with JSON schema
- domain-specific dictionaries
- entity normalization
- entity graph

Entity types to support early:

- PERSON
- ORG
- GPE / LOCATION
- DATE
- EMAIL
- URL
- MONEY
- DOCUMENT_ID
- INVOICE_ID
- CONTRACT_ID
- CUSTOM_TOKEN

---

# 20. Security Model

DokTok2 must treat all of the following as untrusted:

- uploaded/dropped files
- filenames
- extracted text
- OCR output
- PDF metadata
- document chunks
- model output
- tool output
- MCP input
- MCP clients
- MCP servers

## Required controls

- MIME allowlist
- max file size
- max page count
- quarantine folder
- no execution of document content
- no automatic external network access
- no remote AI provider by default
- audit log
- read-only MCP initially
- explicit permissions for future write operations
- safe prompt construction for RAG
- source citation and provenance

## Default posture

```env
DOKTOK_NO_EGRESS=true
```

---

# 21. MCP Strategy

The MCP server should be introduced after ingestion, search, and RAG are working.

The first MCP server must be read-only.

## Initial MCP tools

```text
doktok.search_documents
doktok.get_document
doktok.get_chunk
doktok.ask_documents
doktok.list_entities
doktok.find_related_documents
doktok.get_ingestion_status
```

## Later write tools

Only add later with explicit permissions:

```text
doktok.ingest_file
doktok.add_tag
doktok.create_collection
doktok.reindex_document
```

Do not expose arbitrary SQL.

Do not expose arbitrary filesystem access.

All MCP access should be audited.

---

# 22. API Sketch

## Health

```http
GET /health
```

## Documents

```http
GET /api/documents
GET /api/documents/{document_id}
DELETE /api/documents/{document_id}
```

## Ingestion

```http
GET /api/ingestion/jobs
GET /api/ingestion/jobs/{job_id}
POST /api/ingestion/jobs/{job_id}/retry
POST /api/documents/{document_id}/reindex
```

## Search

```http
GET /api/search?q=...
POST /api/search
```

## Chat

```http
POST /api/chat
```

## Entities

```http
GET /api/entities
GET /api/entities/{entity_id}/documents
```

---

# 23. Frontend Screens

Initial UI should include:

## M0

- app shell
- health/status panel

## M1-M2

- ingestion jobs list
- document list
- document detail

## M3-M5

- failed jobs
- retry controls
- search screen
- entity filters

## M6

- chat with documents
- citation viewer

## M7+

- ingestion dashboard
- reindex controls
- audit events
- MCP settings

---

# 24. Configuration

Recommended environment variables:

```env
DOKTOK_ENV=local
DOKTOK_DATABASE_URL=postgresql://doktok:doktok@localhost:5432/doktok
DOKTOK_FILES_ROOT=./storage/files
DOKTOK_DEFAULT_MODEL=qwen3.6:35b-a3b
DOKTOK_EMBEDDING_MODEL=mxbai-embed-large:latest
DOKTOK_OLLAMA_BASE_URL=http://localhost:11434
DOKTOK_NO_EGRESS=true
DOKTOK_MAX_FILE_MB=200
DOKTOK_MAX_PAGES=500
DOKTOK_FILE_STABILITY_SECONDS=3
```

---

# 25. Milestone Plan

## Rule

Every milestone must produce a functional, runnable version.

Do not merge code that leaves the system unusable.

---

## M0 — Skeleton

Functional result:

- App starts locally.
- Backend health endpoint works.
- UI shell loads.
- PostgreSQL + pgvector runs through Docker Compose.

Scope:

- repo skeleton
- Python workspace
- frontend workspace
- Docker Compose
- config loading
- health endpoint
- minimal UI
- placeholder interfaces
- test/lint/typecheck commands
- README quickstart

Out of scope:

- real ingestion
- real extraction
- real RAG
- MCP server

Acceptance checks:

- `docker compose up` starts Postgres
- backend responds to `/health`
- UI starts
- tests pass

---

## M1 — Folder Ingestion

Functional result:

- User can drop a file into `storage/files/ingest`.
- Worker detects it and creates an ingestion job.

Scope:

- folder watcher
- stable file detection
- atomic move to `in.process`
- SHA-256 hash
- MIME detection by content
- job table
- failed/quarantine handling
- document/job status API

Acceptance checks:

- file is detected
- job is created
- MIME type is recorded
- duplicate hash is handled
- unsupported file goes to failed/quarantine

---

## M2 — Basic Text and PDF Extraction

Functional result:

- Text, Markdown, and born-digital PDFs become active documents.

Scope:

- direct text parser
- direct Markdown parser
- PDF embedded text extraction
- canonical artifacts
- document table
- document detail API
- simple document list UI

Acceptance checks:

- `.txt` becomes searchable text artifact
- `.md` preserves Markdown content
- born-digital PDF text is extracted
- document moves to `docs.active`

---

## M3 — OCR Extraction

Functional result:

- Scanned PDFs and images can be ingested.

Scope:

- PDF classifier
- scanned PDF OCR
- image OCR
- mixed PDF strategy
- OCR confidence storage
- normalized/searchable PDF artifact where applicable

Acceptance checks:

- scanned PDF produces text
- image produces text
- mixed PDFs do not blindly destroy good embedded text
- failed OCR is visible in failed jobs

---

## M4 — Vector and Full-Text Search

Functional result:

- User can search documents semantically and by exact terms.

Scope:

- chunking
- Ollama embedding adapter
- `mxbai-embed-large:latest`
- pgvector storage
- PostgreSQL full-text indexing
- hybrid search API
- search UI

Acceptance checks:

- semantic query returns relevant chunks
- exact keyword query returns matching chunks
- result includes document and page metadata where available

---

## M5 — Entity Indexing

Functional result:

- User can search and filter by entities.

Scope:

- entity extraction adapter
- spaCy or rule-based first implementation
- people/org/location/date/document-id extraction
- entity table
- entity filters
- entity search UI

Acceptance checks:

- entities are extracted from ingested documents
- entity filters work
- entity search returns linked documents/chunks

---

## M6 — Chat with Documents

Functional result:

- User can ask questions against documents and get cited answers.

Scope:

- RAG orchestration
- Ollama chat model adapter
- default model `qwen3.6:35b-a3b`
- document filters
- citation formatter
- chat UI

Acceptance checks:

- answer includes citations
- citations link to source chunks/documents
- system refuses unsupported claims when retrieval is insufficient

---

## M7 — Ingestion Dashboard and Hardening

Functional result:

- User can monitor and manage ingestion.

Scope:

- ingestion dashboard
- retry failed jobs
- reindex document
- delete document
- audit events
- better logs
- metrics endpoint
- admin controls

Acceptance checks:

- failed jobs are visible
- retry works
- reindex works
- audit events are recorded

---

## M8 — Read-Only MCP Server

Functional result:

- External tools can query DokTok2.

Scope:

- MCP server app
- read-only tools
- auth/token support
- audit log for MCP access
- documentation for Claude Code and Copilot usage

Initial tools:

- `doktok.search_documents`
- `doktok.get_document`
- `doktok.get_chunk`
- `doktok.ask_documents`
- `doktok.list_entities`
- `doktok.find_related_documents`
- `doktok.get_ingestion_status`

Acceptance checks:

- MCP client can search documents
- MCP client can retrieve chunks
- MCP access is audited
- MCP is read-only

---

## M9 — Advanced Document Tools

Functional result:

- DokTok2 supports higher-level document intelligence.

Scope:

- summarize document
- summarize collection
- compare documents
- extract timeline
- extract obligations/tasks
- related document discovery
- saved searches

Acceptance checks:

- summaries include citations
- comparisons cite both documents
- timelines cite source pages/chunks

---

## M10 — External Integrations

Functional result:

- DokTok2 can be used as a document knowledge backend by other systems.

Scope:

- PersonalAI integration guide
- Claude Code integration guide
- GitHub Copilot integration guide
- import/export APIs
- optional webhook or watch-folder integrations
- permissions model for future write tools

Acceptance checks:

- external client can query DokTok2 through MCP
- integration docs are complete
- default permissions remain safe and read-only

---

# 26. Architecture Decision Records

## ADR-0001: Modular Monolith and Ports/Adapters

### Status

Proposed

### Context

DokTok2 must remain maintainable by one developer plus coding agents. The system needs clear boundaries for ingestion, extraction, indexing, retrieval, storage, model providers, and MCP access.

A microservice architecture would add unnecessary operational complexity at the start.

### Decision

DokTok2 will be implemented as a modular monolith using ports and adapters.

Core domain logic will depend on interfaces, not infrastructure libraries.

Adapters will implement infrastructure details such as:

- PostgreSQL
- local filesystem
- Ollama
- MIME detection
- OCR tools
- PDF extraction tools
- MCP transport

### Consequences

Positive:

- simpler local development
- easier testing
- easier refactoring
- clear boundaries for coding agents
- future services can be split out if needed

Negative:

- requires discipline to maintain module boundaries
- not horizontally scalable by default

---

## ADR-0002: PostgreSQL + pgvector as the First Storage Spine

### Status

Proposed

### Context

DokTok2 needs relational metadata, ingestion jobs, audit events, full-text search, entity search, and vector search.

Adding multiple databases early would make the system harder to operate.

### Decision

DokTok2 will use PostgreSQL as the first storage spine.

It will use:

- relational tables for metadata
- JSONB for flexible extraction artifacts
- PostgreSQL full-text search for exact and lexical retrieval
- pgvector for semantic retrieval
- normalized tables for entities
- audit tables for sensitive events

### Consequences

Positive:

- one database to operate
- strong transactional guarantees
- good local-first story
- supports hybrid search without extra infrastructure
- easy backups

Negative:

- not as specialized as a dedicated vector database
- may require tuning as document volume grows

---

## ADR-0003: Ollama as the Default Local Model Runtime

### Status

Proposed

### Context

DokTok2 should be local-first and avoid remote AI providers by default.

The system needs:

- chat model access
- embedding model access
- later vision/OCR model access

### Decision

DokTok2 will use Ollama as the default local model runtime.

Defaults:

```env
DOKTOK_DEFAULT_MODEL=qwen3.6:35b-a3b
DOKTOK_EMBEDDING_MODEL=mxbai-embed-large:latest
```

Both models must be configurable.

### Consequences

Positive:

- local-first
- aligns with PersonalAI
- simple developer setup
- avoids remote data exposure by default

Negative:

- user must have sufficient local hardware
- model availability depends on local Ollama installation
- performance varies by machine

---

## ADR-0004: Folder-Based Ingestion with Database Job State

### Status

Proposed

### Context

The desired workflow is simple: the user drops files into an ingest folder and DokTok2 processes them.

Folders alone are not enough to track retries, status, failures, deduplication, and auditability.

### Decision

DokTok2 will use folder-based ingestion plus database-backed job state.

Folder lifecycle:

```text
storage/files/ingest
storage/files/in.process
storage/files/docs.active
storage/files/docs.failed
storage/files/quarantine
```

Database lifecycle:

```text
queued
detecting
hashing
normalizing
extracting
chunking
embedding
indexing
activating
active
failed
quarantined
```

### Consequences

Positive:

- simple user workflow
- reliable processing state
- retry support
- good observability
- failure handling

Negative:

- requires careful coordination between filesystem and database state
- needs idempotent processing

### Implementation notes

- Wait until files are stable before processing.
- Use atomic moves.
- Compute SHA-256 for deduplication.
- Never mark a document active before indexing succeeds.

---

## ADR-0005: Hybrid Retrieval from the First Search Milestone

### Status

Proposed

### Context

Document management requires both semantic search and exact search.

Users search for:

- concepts
- names
- invoice numbers
- contract identifiers
- dates
- filenames
- organizations
- places
- exact phrases

Vector search alone is not enough.

### Decision

DokTok2 will implement hybrid retrieval from the first search milestone.

The retrieval stack will combine:

1. pgvector semantic search
2. PostgreSQL full-text search
3. entity/token search

Later improvements may include reranking and query expansion.

### Consequences

Positive:

- better search quality
- works for exact terms and semantic questions
- stays inside PostgreSQL
- supports RAG citations

Negative:

- scoring and ranking are more complex
- requires tuning

### Implementation notes

Search results should return:

- document id
- chunk id
- page number if available
- title/filename
- snippet
- score components
- extraction method

---

## ADR-0006: Local-First and No-Egress-by-Default Security Posture

### Status

Proposed

### Context

DokTok2 processes private documents. It must be safe by default.

Risks include:

- malicious files
- prompt injection inside documents
- accidental remote model calls
- MCP overexposure
- unsafe file handling
- leaking document contents to external systems

### Decision

DokTok2 will be local-first and no-egress by default.

Default behavior:

- local filesystem storage
- local PostgreSQL
- local Ollama
- no remote AI providers
- no external network calls unless explicitly configured
- read-only MCP server first
- audit all sensitive operations

All document content, extracted text, model output, tool output, and MCP input must be treated as untrusted.

### Consequences

Positive:

- privacy-preserving default
- safer local deployment
- clearer trust model
- suitable for sensitive documents

Negative:

- fewer cloud conveniences by default
- user must explicitly configure integrations

### Required controls

- MIME allowlist
- file size limits
- page count limits
- quarantine folder
- no execution of document content
- audit log
- explicit permission for future write tools
- explicit configuration for any remote provider

---

# 27. First Claude Code Task

Claude Code, your first task is to implement **M0 only**.

Do not implement ingestion yet except for contracts, interfaces, and empty adapters.

## Required steps

1. Inspect the PersonalAI repository structure and summarize which patterns should be reused.
2. Create a DokTok2 architecture proposal in:

```text
docs/architecture/doktok2-architecture.md
```

3. Create ADRs:

```text
docs/adr/ADR-0001-modular-monolith-and-ports-adapters.md
docs/adr/ADR-0002-postgresql-pgvector-storage-spine.md
docs/adr/ADR-0003-ollama-default-local-model-runtime.md
docs/adr/ADR-0004-folder-ingestion-with-db-job-state.md
docs/adr/ADR-0005-hybrid-retrieval-first-search-milestone.md
docs/adr/ADR-0006-local-first-no-egress-security.md
```

4. Create milestone documentation:

```text
docs/milestones/M0-M10.md
```

5. Create the initial repo skeleton with placeholder packages and tests.

6. Implement M0 only:

- Docker Compose with PostgreSQL + pgvector
- FastAPI health endpoint
- minimal React UI shell
- config loading
- test/lint/typecheck commands
- README quickstart

7. Do not implement ingestion yet beyond interfaces/contracts.

8. Keep the system functional and shippable.

9. Run tests, typechecks, and lints where available.

10. At the end, report:

- files created/changed
- commands run
- tests passed/failed
- next recommended implementation step

---

# 28. M0 Acceptance Criteria

M0 is complete when:

```text
docker compose up
```

starts PostgreSQL + pgvector successfully.

Backend health endpoint works:

```http
GET /health
```

The UI starts and shows a DokTok2 shell.

The repo includes:

```text
README.md
docs/architecture/doktok2-architecture.md
docs/milestones/M0-M10.md
docs/adr/*.md
docker-compose.yml
pyproject.toml
package.json
pnpm-workspace.yaml
apps/backend
apps/ui
apps/worker
apps/mcp
core
contracts
providers
storage
modalities
retrieval
tools
```

The project has placeholder tests and commands for:

- backend tests
- backend lint
- backend typecheck
- frontend tests
- frontend lint
- frontend typecheck

---

# 29. Quality Bar for Claude Code

Keep the architecture slim.

Prefer boring proven tools.

Avoid microservices.

Avoid premature infrastructure:

- no Kubernetes
- no Redis unless needed later
- no Elasticsearch unless PostgreSQL search is insufficient
- no Qdrant unless pgvector is insufficient
- no MinIO unless filesystem storage is insufficient
- no graph database unless entity relationships justify it

Use adapters so these can be added later without rewriting core.

Make the system understandable and maintainable by one developer plus coding agents.

Do not overbuild.

Do not implement multiple milestones in one pass.

Do not claim success without running available checks.

---

# 30. Suggested Commit Plan

## Commit 1

```text
docs: add DokTok2 architecture, ADRs, and milestone roadmap
```

## Commit 2

```text
chore: create monorepo skeleton
```

## Commit 3

```text
feat: add backend health endpoint and config
```

## Commit 4

```text
feat: add minimal UI shell
```

## Commit 5

```text
chore: add docker compose and developer commands
```

## Commit 6

```text
test: add placeholder backend and frontend checks
```

---

# 31. Next Step After M0

After M0 is complete, implement M1:

> Folder ingestion with stable file detection, atomic move to `in.process`, SHA-256 hashing, MIME detection by content, database-backed ingestion jobs, failed/quarantine handling, and basic ingestion status UI.

Do not begin M1 until M0 is functional.
