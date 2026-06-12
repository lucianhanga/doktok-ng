# ADR-0011: Document enrichment and structured records

## Status

Accepted

## Context

Beyond extraction + retrieval, documents need human-facing metadata (title, document date, location,
summary), multi-label categories, and — for aggregation questions ("how much did I spend at Block
House across all statements?") — typed transactional records. RAG over chunks cannot answer
enumeration/aggregation reliably because the data is scattered across the corpus.

## Decision

Add enrichment as **versioned, idempotent feature processors** under the reconciliation framework
(ADR-0009), each re-derivable from the stored `content.md`:

- **`doc_metadata`** — title / document_date (normalized `YYYY-MM-DD` or `n/a`) / location / summary.
  The title and summary are written in the document's own language.
- **`doc_classify`** — up to 5 multi-label categories per document, reusing a tenant-wide vocabulary
  capped at 20 (enforced by DB triggers).
- **`structured_records`** — typed transactions/line items into `extracted_records`, with **money as
  integer minor units** (never float) plus a required currency, a `record_type`, and a JSONB payload.

Structured extraction uses the dense enrichment model with strict JSON `format`, a JSON-repair
fallback, and hard validation in core (see ADR-0003 for the model strategy). All model/OCR content is
treated as untrusted data, never instructions.

The aggregation **query path** (typed intents + a `RecordRepository.aggregate` over the typed money
spine, with `pg_trgm` merchant resolution) is the consumer of `extracted_records` and is tracked
separately.

## Consequences

- Documents gain rich, language-faithful metadata and become categorizable and aggregatable.
- Enrichment inherits reconciliation's backfill/retry/versioning, so improving a processor (e.g. a
  prompt or model change) re-derives the whole corpus via a version bump.
- Money is exact (integer minor units), so aggregation sums are correct by construction.
