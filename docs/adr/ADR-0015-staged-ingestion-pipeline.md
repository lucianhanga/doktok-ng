# ADR-0015: Staged ingestion pipeline (intake as resumable ledger stages)

## Status

Accepted

The three flagged design points are resolved to this ADR's recommended path:

1. **Eventual consistency — accepted.** A document is visible (`active`) once `extract` completes;
   `chunk_embed` runs as a gated stage right after, so search inclusion is eventually consistent
   (shown by the badge). This honours the chosen "visible once extraction completes" behaviour and
   keeps maximum decoupling.
2. **Per-stage concurrency — phased.** First cut uses the global `reconcile_concurrency` + the OCR
   predictor pool; a dedicated `DOKTOK_OCR_CONCURRENCY` per-stage cap follows.
3. **Cutover — flag-gated/shadow.** A `staged_ingestion` flag (default off) gates the new path so it
   can be built and proven without changing default behaviour, then the default is flipped.

## Context

Ingestion is today a **hybrid**:

- **Intake → activation is a monolithic per-file function.** `core/doktok_core/ingestion/pipeline.py`
  (`process_file` / `_activate`) runs, in order, for one file: MIME detect → SHA-256 hash →
  content-dedup check → security/size gate → **extract** (born-digital PDF text via PyMuPDF, else
  **OCR** via PaddleOCR) → write canonical artifacts → create the `documents` row (FK-order, ADR-0012)
  → run the inline features (extract marker, `chunk_embed`, `entities`). Files are processed in
  parallel across the ingest folder (`ingest_concurrency`). On any failure it calls `_fail`: the
  document is recorded `failed` and the original relocated to `docs.failed/`.
- **Post-activation features already are a decoupled, parallel, resumable, badged pipeline** — the
  ADR-0009 reconciler: a `document_features` ledger (one row per document × feature),
  `claim_next FOR UPDATE SKIP LOCKED` with a lease, retry-with-backoff + `max_attempts`, versioned
  idempotent `FeatureProcessor`s, and a per-document badge = `document_features.status`.

So the *features* layer is exactly the model we want; the *intake/extraction* path is not. If OCR or
extraction fails, the whole job fails and the file re-queues **from the start** (no resume from the
failed stage), OCR has **no badge of its own**, and OCR concurrency is implicit (coupled to
`ingest_concurrency`).

Two foundations are already merged toward this:

- **#210** — PaddleOCR runs a **pool of independent predictors** (parallel OCR), no global OCR lock.
- **#211** — the ledger gained a **stage-dependency primitive**: `FeatureProcessor`s declare
  `dependencies`, and `claim_next` only claims a stage once every prerequisite has a `done` row on the
  same document (gated in SQL; empty dependencies = no gating, so current behavior is unchanged).

Two product decisions were taken for the target model:

1. **Visibility:** a document appears in the library **only after extraction completes** (it surfaces
   as `active`); the intake/OCR/extract phase is behind the scenes.
2. **Failure:** every stage is **retryable** — auto-retry with backoff, resume from the failed stage,
   and only go terminal `failed` after exhausting attempts. A document is never silently lost.

## Decision

Make the **entire** post-intake pipeline a DAG of resumable **ledger stages**, reusing the ADR-0009
machinery. The cheap, tightly-coupled intake prefix stays inline; the expensive stages (OCR/extract,
and the existing features) become first-class nodes with badges, dependencies, retries, and
resume-from-stage. No external workflow engine — the Postgres ledger is the durable executor.

### 1. Document lifecycle

```
            intake (inline)                 extract stage              feature stages
file ──▶ detect/hash/dedup/security ──▶ [doc: processing] ──▶ OCR/text+artifacts ──▶ [doc: active] ──▶ chunk_embed
                                          + seed stage ledger        (extract done)                    entities
                                          + original in workdir                                        doc_metadata
                                                                                                       doc_classify
                                                                                                       structured_records
                                                                                                       thumbnail
```

`documents.status`:

- **`processing`** — created at intake; **hidden from the library** (the Documents list already filters
  to `active`, so this needs no UI change). Has a stage ledger.
- **`active`** — set by the `extract` stage on success: artifacts exist, content is viewable. It now
  appears in the library; the dependency gate lets the feature stages start.
- **`failed`** — terminal, only after a stage exhausts `max_attempts`. Still a real `documents` row
  with its ledger (resettable / reingestable), surfaced under the Failed filter.
- `duplicate` — unchanged (content-dedup).

### 2. Lightweight intake (stays inline)

At folder pickup, the worker does only the cheap, coupled steps and then hands off to the ledger:

1. MIME detect, SHA-256 hash, security/size gate (unchanged).
2. **Content-dedup at intake**: `find_active_by_sha256` → if an active document already holds this
   content, record a `duplicate` and stop (as today).
3. Move the original into a per-document **workdir** (`docs.processing/<doc_id>/original.<ext>`).
4. Create the `documents` row as **`processing`** (sha256 known) and **seed the stage ledger**: an
   `extract` row (`pending`) plus the six feature rows (`pending`, each gated on `extract` via #211).
5. Return — the reconciler drives the rest. (No OCR/extract on the intake thread anymore.)

### 3. `extract` becomes a real stage processor

Today `extract` is an inline *marker* (`record_done` at activation). It becomes an `ExtractStage`
`FeatureProcessor` (`name="extract"`, no dependencies — it's the DAG root):

- Reads the original from the workdir; runs the existing extraction service (born-digital text or OCR
  via the M-B predictor pool), producing pages + the normalized/searchable PDF.
- Writes the canonical artifacts to `docs.active/<doc_id>/` (content.md/json, pages, normalized,
  original.pdf), exactly as `_activate` does today.
- **Activates** the document: flips `processing → active` (handling the `uq_documents_active_sha` race
  → `DuplicateActiveDocumentError` → mark `duplicate`, as today).
- Marks the `extract` row `done` → the gated feature stages become claimable.

Because the original is preserved under the document dir, `extract` is now itself **idempotent and
reprocessable** (re-OCR from the stored original) — a free consequence; it can join the reprocess
dropdown later.

### 4. Feature stages decouple from activation

`chunk_embed` and `entities` no longer run *inline* at activation; they run as their own gated stages
(they are already `FeatureProcessor`s). Consequence: a document is **viewable** (content present) the
moment it's `active`, but full-text/vector **search includes it once `chunk_embed` is `done`** — a
short eventual-consistency window, visible via the badge. This matches decision 1 ("visible once it
has searchable content" = extraction complete) and maximizes decoupling.

### 5. Failure & retry (decision 2)

A stage failure marks its row `failed` with backoff + `next_attempt_at`; `claim_next` re-offers it
until `max_attempts`. Resume is from the failed stage only (prior stages stay `done`; their artifacts
on disk are the checkpoint). Terminal exhaustion sets the row `failed`; for the `extract` root, that
also sets `documents.status = failed`. **No more silent `docs.failed` move** — the document and its
ledger remain, so the existing reingest/reset actions and the new auto-retry both work. (The original
stays in the doc's dir for re-extraction.)

### 6. OCR page-level checkpointing (folds in M-A)

Inside `ExtractStage`, persist each OCR'd page to the workdir as it completes. A retry skips pages that
already have a checkpoint and resumes from the first missing page — so a failure on page 40 of 50
doesn't redo 39 pages. Zero schema change (disk is the checkpoint).

### 7. Per-stage concurrency

OCR parallelism is already bounded by the predictor pool (#210). The reconciler's single global
`reconcile_concurrency` is extended to **per-stage caps** so the expensive `extract`/OCR stage gets its
own limit (`DOKTOK_OCR_CONCURRENCY`), independent of the cheap LLM/embedding feature stages. The
reconciler tracks in-flight counts per stage and skips a stage at its cap when claiming. (If this
proves fiddly, the first cut can ship with the global cap + the OCR pool and add per-stage caps next.)

## Migration & backward compatibility

- **Existing documents are untouched.** They are already `active` with `extract` rows `done`
  (back-filled earlier) and feature rows `done`; `ensure_for_active` won't re-run anything (versions
  match). The dependency gate is satisfied (extract done).
- **`extract` joins the catalog** as a real reprocessable stage (version 1, matching the existing
  done rows, so no mass re-extraction).
- **Schema:** none strictly required — `documents.status` already has `processing`; the stage ledger is
  the existing `document_features`. A small migration may add a partial index for claiming `extract`
  rows and (optional) a `documents.workdir`/`original_rel` column if the original location isn't already
  derivable. Dedup keeps `uq_documents_active_sha` (processing docs are exempt; the race is resolved at
  activation, as today).
- **Rollback:** the change is behind the worker composition; reverting restores the monolithic
  `process_file`. In-flight `processing` docs would need a one-off requeue.

## UI implications

- Documents list already shows only `active` → `processing` docs are naturally hidden. No change.
- The **Failed filter** now shows terminally-failed docs (ledger-tracked) instead of `docs.failed`
  orphans — strictly better.
- Badges gain `extract`/OCR as a first-class stage (the chip already exists; the catalog entry makes it
  reprocessable). The Overview "Pending features" / Ingestion counts now also reflect in-flight extract.

## Rollout (PR-sized, foundation-first)

1. **`ExtractStage` processor** behind a flag, plus intake that creates a `processing` doc + seeds the
   ledger + workdir — *without* yet removing the inline path (dual-write, shadow).
2. **Cut intake over** to the staged path; `_activate`'s extract/OCR work moves into `ExtractStage`;
   remove the inline OCR/extract from `process_file`. Decouple `chunk_embed`/`entities` to gated stages.
3. **Failure model**: retryable extract + terminal-failed (drop the silent `docs.failed` move);
   reconcile the reingest/reset actions.
4. **Per-stage concurrency** (`DOKTOK_OCR_CONCURRENCY`) + **OCR page checkpointing** (M-A).

Each step ships `make check`-green with tests (in-memory oracle for the ledger; the existing OCR/extract
fakes for `ExtractStage`).

## Consequences

**Positive**

- One uniform, decoupled, resumable, badged pipeline end-to-end; OCR/extract get resume-from-stage,
  their own concurrency cap, and visibility — the user's 7-point model, fully.
- Failures self-heal and are never silently lost; reprocessing extends to extraction.
- No new infra: the Postgres ledger is the durable executor; reuses ADR-0009 + #210/#211.

**Negative / costs**

- A real change to the **document lifecycle** (`processing` state, activation moves into a stage) and
  the intake code — the riskiest part; needs careful tests and the shadow/dual-write rollout.
- A short **eventual-consistency** window: an `active` doc isn't search-indexed until `chunk_embed`
  completes (mitigated by badges; acceptable for a local single-user app).
- Per-stage concurrency adds bookkeeping to the reconciler.

## Alternatives considered

- **Keep the hybrid** (do nothing): simplest, but OCR/extract stay non-resumable, unbadged, and a
  failed OCR re-runs from scratch — the exact gap this ADR closes.
- **External workflow engine** (Temporal / Prefect / LangGraph): over-engineered for a local,
  single-user, single-box app; the ledger already provides durable, idempotent, retryable execution.
- **Job-ledger for extract, feature-ledger for the rest** (formalize the current split): two mechanisms
  to reason about; unifying on one ledger is simpler and is what #211 enables.

## References

- ADR-0009 (document feature reconciliation — the ledger this extends)
- ADR-0012 (referential integrity & activation ordering — the FK-order constraint to preserve)
- ADR-0004 (folder ingestion + DB job state), ADR-0003 (OCR)
- PRs #210 (parallel OCR pool) and #211 (stage-dependency primitive)
