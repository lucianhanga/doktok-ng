# ADR-0017: Unidentifiable-document marker

## Status

Accepted

## Context

A scanned image (`0000548.jpg`) ingested successfully (`status=active`, OCR ran) but is meaningless
- codes/numbers, no coherent text. The enrichment LLM titled it **"Unidentifiable Document"** with a
summary "content is largely unrecognizable", and the classifier still attached **four spurious
categories**. Today the only signal that a document is unidentifiable is that fragile, English,
model-written **title string** - there is no structured field to filter on or highlight.

We want to (a) **filter** for these documents and (b) **highlight** them, without breaking the ~2000+
already-ingested documents or disrupting ingestion that is in progress.

An "unidentifiable" document is **not a failure** - extraction succeeded. It is the classifier
**abstain / reject-option** pattern: in-distribution for ingestion, out-of-distribution for
meaningful enrichment. So it must live *outside* the document lifecycle status (it stays `active`)
and *outside* the user's category taxonomy (which is exactly why `0000548` got noise categories).

## Decision

Add a structured, queryable marker and detect it from a robust signal rather than the title string.
Roll it out in two phases so the additive part ships with zero ingestion impact.

### Representation
- A nullable **`documents.unidentifiable boolean`** - tri-state: `TRUE` (confirmed unidentifiable),
  `FALSE` (confirmed identifiable), `NULL` (not yet assessed / pre-migration). The boolean is the
  single **queryable source of truth** for filtering.
- If a numeric **content-confidence** is wanted for UX/audit, it lives in the existing `metadata`
  JSONB (`metadata.content_confidence`), not a new column - the extractor derives the boolean from
  it at write time.
- **Not** a reserved "Unidentifiable" category (junk-drawer anti-pattern that pollutes the taxonomy),
  **not** a new document status (extraction succeeded), **not** a bare title convention.

### Detection (Phase 2)
- Do **not** depend on the model emitting the literal "Unidentifiable" string. Combine a
  **deterministic backstop** (the OCR `text_quality` already computed, and/or a cheap
  low-text-length / high-non-alphanumeric heuristic) **OR'd** with an **explicit structured field**
  the enrichment extractor emits, fused conservatively in core. Local-first, no extra egress.
- When a document is flagged unidentifiable, **suppress `doc_classify`** (formalize the
  `doc_metadata -> doc_classify` dependency) so it never accrues spurious categories.

### UX
- A **first-class filter facet** on the Documents list (alongside status / category /
  needs-attention): show-all / only-unidentifiable / exclude.
- A **neutral (non-red) badge** - red stays reserved for actual failures - with a manual override for
  false positives (later).

### Backfill
- A one-time, idempotent migration `UPDATE ... SET unidentifiable=TRUE WHERE status='active' AND
  title='Unidentifiable Document'` so existing docs become filterable immediately. Going forward the
  extractor sets the flag explicitly; a later feature-version bump can re-assess the whole corpus
  from the real signal (not the title) - no re-ingest.

## Consequences / rollout

- **Phase 1 (additive, no ingestion impact):** migration `0022` (nullable boolean - a catalog-only
  `ADD COLUMN`, no table rewrite - + partial index `WHERE unidentifiable IS TRUE` + the title-match
  backfill); `Document.unidentifiable`; an `unidentifiable` filter on `list_documents` /
  `list_document_ids` + API param; the badge + filter facet. Every documents SELECT uses an explicit
  column list, so the new column is purely additive. Deployable by **restarting the backend only**;
  the running worker keeps ingesting (new rows get `NULL`). Existing data stays valid.
- **Phase 2 (behavior-changing, deliberate):** the detection signal in core/provider + classify
  suppression, shipped via a `doc_metadata` feature-version bump so the reconciler re-assesses on its
  own cadence (`SKIP LOCKED`), with no re-ingest and safe under a mid-batch restart (stale-job
  recovery re-queues interrupted work).
- The boolean is forward-compatible: a richer `content_class` could be added additively later.
- Tradeoff: `NULL` ("unassessed") is treated as "not unidentifiable" by the exclude filter, so a
  doc the model has not yet judged is never hidden.

## Hand-offs

- **database-architect:** column + migration `0022` + partial index + backfill (designed).
- **agentic-ai-architect / LLM-Ollama:** the Phase-2 detection fusion, abstain prompt, threshold.
- **ui-developer / ui-ux:** the neutral badge + filter facet.
