# Document processing consistency audit (2026-10-25)

Triggered by the OOM incidents (2026-10-24/25) and the question "why don't failed documents show
up in Documents?". Full read of the intake pipeline (`core/doktok_core/ingestion/pipeline.py`),
the staged extract stage (`extract_stage.py`), the feature reconciler
(`core/doktok_core/features/reconciler.py`), the worker loops (`apps/worker/doktok_worker/worker.py`),
and the Postgres adapters.

## The three state machines

**Ingestion job** (intake, `process_file`):
`queued → detecting → hashing →` then exactly one terminal: `duplicate` | `quarantined` |
`failed` (reject) | `active`. With staged ingestion (ADR-0015) the job goes terminal `active` at
intake and the document's stage ledger drives the rest. Crash/SIGKILL anywhere → the stale-job
sweep (`recover_stale_jobs`) tombstones the job `failed(worker_crash_recovered)` and re-queues the
file; past `MAX_STALE_RECOVERIES` it is quarantined (`crash_loop`).

**Document**: created either terminal at intake (`failed`/`duplicate`) or as `processing`
(staged), then the extract stage moves it to `active` or `failed`
(`UPDATE ... WHERE status='processing'` — terminal writes are guarded).

**Document feature** (ADR-0009 ledger): `pending → running → done`, or `failed` with exponential
backoff until `max_attempts` (3). Stale `running` rows are reclaimed by lease; startup
`requeue_running` resets orphans; #850 bounds crash-reclaims (exhausted → `failed`).

## The invariant (target)

**Every accepted file ends with exactly one terminal document row** — `active`, `failed`,
`duplicate`, or `quarantined` — and the Overview shows only non-terminal work. Terminal failures
are visible and reprocessable from Documents.

## Findings

| # | Severity | Finding |
|---|----------|---------|
| F1 | **high** | `_quarantine` (security policy, intake) creates **no document row** — quarantined files are invisible in Documents. `_fail` and `_handle_duplicate` do create terminal rows; quarantine is the odd one out. |
| F2 | **high** | `_quarantine_crash_loop` (crash tripwire) creates **no document row** either. |
| F3 | **high** | Zombie `processing` documents: a worker SIGKILL during the extract stage leaves the document `processing` forever — the job is already terminal (`active` at the staged handoff), so job recovery never fires, and the feature ledger's exhausted `extract` row is never propagated to the document. |
| F4 | medium | Crash inside `_create_processing_document`'s narrow window (document inserted, job not yet updated) leaves a zombie `processing` document plus an in-flight job; recovery tombstones the job, the document stays. |
| F5 | low | The bounded sweep (#850) fails an exhausted feature row but nothing marks the parent document failed when the exhausted feature is `extract` (same root as F3). |
| F6 | low | Documents UI: the status filter lacks `quarantined`; failed documents have no direct Reprocess affordance in the list (reingest exists in DocumentDetail + bulk action, endpoint has no status guard). |
| F7 | info | Overview in-flight pinned terminal rows (pre-intake failures) — a workaround for F1/F2; becomes obsolete once terminal documents exist for every outcome. Legacy tombstones remain dismissible (DELETE /jobs, #853). |

## Verified consistent (no action)

- Activation order: artifacts → document row → indexing; indexing failure deletes the row and
  fails the job into `docs.failed/` (original preserved → reingestable).
- Active-sha race: partial unique index + `DuplicateActiveDocumentError` → duplicate recorded.
- Dedup source of truth is the documents table, not the job ledger.
- `document_features` FK-cascades on document delete (migration 0013); job rows are deleted with
  the document (`delete_for_document`).
- Feature failures: backoff + `max_attempts`; crash reclaim bounded (#850); startup orphan requeue.
- Lease window (30 min) sits above worst-case feature runtime — no live-work reclaim.

## Fixes (this PR series)

- F1/F2: quarantine paths create terminal `quarantined` document rows (with error metadata +
  file location), mirroring `_fail`.
- F3/F4/F5: a worker sweep fails `processing` documents whose `extract` feature is terminally
  failed (or missing long past intake) — with tests proving legit in-flight work is untouched.
- F6: Documents status filter gains `quarantined`; failed rows get a Reprocess action.
- F7: no Overview change needed — the pinned-row path self-obsoletes once F1/F2 land.
