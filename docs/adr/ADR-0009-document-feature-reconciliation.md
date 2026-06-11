# ADR-0009: Document feature reconciliation (per-feature processing ledger)

## Status

Accepted

## Context

A document's `status=active` currently means "extracted and indexed by *today's* pipeline". When a new
processing capability is added later (e.g. RAG index, summaries, classification), already-active
documents silently lack it, with no mechanism to backfill them, resume after a crash, retry failures,
or surface per-capability errors. Processing is also a one-shot inline pipeline, so a failure in one
enrichment step fails the whole document.

We also need this to **scale horizontally**: it must be possible to run several worker instances
(spawned on demand under load) that cooperate without double-processing or lost work.

## Decision

Adopt a **desired-state reconciliation** model (a controller loop) over a per-document, per-feature
ledger, instead of a one-shot pipeline for enrichment.

- A **feature registry** in code lists every processing capability as a `FeatureProcessor`
  (`name`, `version`, idempotent `process(tenant_id, document_id)`).
- A **`document_features` ledger** table records, per `(tenant, document, feature)`: `status`
  (pending/running/done/failed), `feature_version`, `attempts`, `last_error`, and backoff timestamps.
- A **reconciler** drives observed -> desired: every active document should have every registered
  feature at its current version in `done`. Missing rows are created `pending`; due rows are claimed
  and run; success -> `done`, failure -> `failed` with backoff and a bounded retry count.

Document lifecycle is split:

- **Blocking stage (synchronous, unchanged):** receive -> identify -> security -> dedup -> extract ->
  artifacts -> `Document(status=active)`. "Active" = *the document exists with extracted content*.
- **Additive features (reconciled):** chunking/embeddings, entities, and future capabilities are
  tracked in the ledger. A failure in one feature no longer fails the document; it is retried.

### Horizontal scalability (multiple workers)

This is the load-bearing requirement and shapes the design:

- **Workers are stateless.** All processing state lives in Postgres (`document_features`); a worker
  holds nothing in memory that another worker needs.
- **Atomic work claiming** uses `SELECT ... FOR UPDATE SKIP LOCKED` (the standard Postgres queue
  pattern): each worker claims distinct due rows without contention, so N workers process N features
  in parallel with no double-processing.
- **Leases + reclamation:** a claimed row is marked `running` with `last_attempt_at`; a row stuck in
  `running` beyond a lease timeout (its worker died) is reclaimed to `pending` by any worker.
- **Idempotent processors:** every `process()` deletes its prior outputs then rewrites
  (`delete_for_document` already exists for chunks and entities), so a reclaimed/retried run is safe.
- **No leader required:** any number of workers run the same reconcile loop; correctness comes from
  the DB-level claiming, not coordination. Workers can be added/removed freely under load.

### Retry, resume, manual re-run

- **Retry/backoff:** `attempts` + exponential backoff (`next_attempt_at`); after `max_attempts` the
  row is terminal `failed` with `last_error`.
- **Resume after restart:** state is durable; a restarted worker re-scans and picks up pending /
  retryable / reclaimable rows.
- **Manual re-run:** an API resets a feature row to `pending` (attempts=0) so the user can retry after
  fixing the cause.
- **Version bump:** raising a feature's `version` makes existing `done` rows stale -> reprocessed,
  which is how an improved implementation is rolled out across the corpus.

### API + UI

- `GET /api/v1/documents/{id}/features` - per-feature status.
- `POST /api/v1/documents/{id}/features/{feature}/retry` - reset to pending.
- Document detail shows a "Processing" panel (status badge + error + Retry per feature).

## Consequences

Positive: new features backfill automatically; crash-safe and resumable; per-feature retry and error
visibility; horizontally scalable workers; enrichment failures no longer fail the whole document.

Negative: more moving parts than an inline pipeline; processors must be idempotent and version-aware;
a document can be `active` before all enrichments finish (the UI exposes per-feature state, which is
the intended, more honest model).

## Rollout (phased)

1. Ledger table + repository (SKIP LOCKED claim, ensure, mark, reset, reclaim); `FeatureProcessor`
   registry; reconciler loop in the worker; record current features (`extract`, `chunk_embed`,
   `entities`) and backfill any active document missing them.
2. Feature status API + Document-detail Processing panel + per-feature retry.
3. Version-bump reprocessing, stale-`running` reclamation tuning, audit events.
4. New capabilities (RAG index, summaries, classification) land as registered features and backfill
   automatically.
