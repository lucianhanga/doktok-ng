# ADR-0019: Office documents via a local Gotenberg normalizer

## Status

Accepted

## Context

Through M7 the pipeline ingested PDFs and images only. Microsoft Office OOXML documents
(`.docx`/`.xlsx`/`.pptx`) are a large, common part of a real document corpus, and DokTok NG needs to
index, search, preview, and chat over them. Two options existed: (a) parse each OOXML format
natively per type, or (b) convert each to PDF once and reuse the mature canonical PDF path (extract /
render / OCR / thumbnail / preview). Native per-format parsing means three parallel extraction code
paths, three thumbnail/preview stories, and no shared OCR fallback. The local-first, no-egress
constraint (ADR-0006) rules out any cloud conversion service: document content must never leave the
host.

## Decision

Convert office documents to PDF **on ingest** with a `DocumentNormalizer` port, then run the existing
PDF path on the result. The single adapter is `GotenbergNormalizer`, backed by a local
[Gotenberg](https://gotenberg.dev) container.

- **Port:** `DocumentNormalizer.to_pdf(path, mime) -> bytes` (`contracts/.../ports.py`). Core depends
  only on the port (ADR-0001); the engine is swappable.
- **Adapter:** `GotenbergNormalizer` (`modalities/files/.../normalize.py`) POSTs the file to
  Gotenberg's `/forms/libreoffice/convert` route and returns the PDF bytes. Wired in
  `apps/worker/.../composition.py` and `core/.../ingestion/pipeline.py`.
- **Routing:** the extraction service (`core/.../extraction/service.py`) recognizes the three OOXML
  MIME types (detected by content, not extension), normalizes to a PDF, and reuses `_extract_pdf`. The
  converted PDF is persisted as the normalized/system document; if it itself needs OCR, the searchable
  PDF carrying the recovered text layer is stored instead.
- **Engine choice:** Gotenberg (`gotenberg/gotenberg:8`) wraps headless LibreOffice, is **MIT-licensed**,
  and is published as an official Docker image. It is the **single** added engine, chosen for a clean
  supply chain and fully local conversion. It runs as a `gotenberg` service in `docker-compose.yml`, so
  document content never leaves the host.
- **Settings:** `DOKTOK_GOTENBERG_URL` (default `http://localhost:3000`) points the worker at the
  container; `DOKTOK_GOTENBERG_PORT` (default `3000`) overrides the compose host port to avoid clashes.
- **Preview/download:** in-browser preview and "Open in new tab" use the normalized PDF (the viewable
  form); **Download** returns the **original** office file, preserved byte-for-byte. Thumbnails, page
  images, and the OCR text-region overlay derive from the system document, so they work uniformly.

## Consequences

- One extraction/preview/thumbnail/OCR path serves PDFs, images, and office documents; no parallel
  per-format pipelines.
- The local-dev stack gains a `gotenberg` service. It comes up with `docker compose up` alongside the
  database; if it is unreachable, office ingestion fails with `needs_ocr`.
- A new external runtime dependency (Gotenberg/LibreOffice), accepted as a single MIT-licensed,
  Docker-published, fully local engine consistent with ADR-0006 (no egress).
- The original file is always retained, so a future native-OOXML extractor could be added behind the
  same port without re-ingesting.

## Alternatives considered

- **Native per-format parsing** (python-docx / openpyxl / python-pptx): no shared preview/thumbnail or
  OCR fallback, three code paths to maintain. Rejected for complexity.
- **Cloud conversion API:** violates the local-first / no-egress default (ADR-0006). Rejected.

## Related files

- `contracts/doktok_contracts/ports.py` (`DocumentNormalizer`)
- `modalities/files/doktok_modalities_files/normalize.py` (`GotenbergNormalizer`)
- `core/doktok_core/extraction/service.py` (OOXML routing)
- `apps/worker/doktok_worker/composition.py`, `core/doktok_core/ingestion/pipeline.py` (wiring)
- `core/doktok_core/security/policy.py` (OOXML MIME allowlist)
- `core/doktok_core/config.py` (`gotenberg_url`)
- `docker-compose.yml` (`gotenberg` service)

## Date

2026-06-15
