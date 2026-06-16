# Backup/restore consistency model (application level)

How the document database and the `files_root` tree stay consistent across backup and restore, what
the application guarantees, and what it re-derives or repairs (M12 app-consistency tickets). The
Postgres PITR/WAL mechanics and the offsite transport/scheduling live in the backup-and-recovery
runbook (M12 DB/DEVOPS tickets); this is the app contract those rely on.

## What an active document is

Two coupled things:

- **A Postgres row set** — `documents` plus its `ON DELETE CASCADE` children (`document_chunks` +
  embeddings, `document_entities`, `document_features` ledger, `document_category_links`,
  `extracted_records`, `document_activity`).
- **A `files_root` directory** — `docs.active/{document_id}/` with `original.<ext>`, `manifest.json`,
  `content.md`, `content.json`, `pages/`, `normalized/searchable.pdf`, `thumbnails/thumb.webp`.

**Irreplaceable vs derived:** the **`original.<ext>` is the only irreplaceable byte stream.**
Everything else — content/pages/normalized PDF/thumbnail on disk, and chunks/embeddings/entities/
metadata/categories/records in the DB — is **re-derivable** from the original by re-extraction +
the feature reconciler (ADR-0009). Backups therefore must never lose the original; derived data is a
performance/restore-time concern, not a durability one.

## Ordering invariant: files lead the row

In both ingestion paths the artifact files are written (and now fsync'd, APP-C1) **before** the
`documents` row becomes `active`. So the only safe restore posture is **the database restored to a
point no later than the files**:

- DB behind files (a directory with no row) → **benign**: orphan bytes nothing reads; re-ingestable.
- DB ahead of files (a row whose artifacts are missing) → **the dangerous direction**: the app
  expects to read artifacts that aren't there.

**Restore rule:** restore `files_root` to a point **>= the DB restore point**. With continuous
`files_root` snapshots and Postgres PITR, pick the DB target time at or before the latest files
snapshot.

`LocalFileStorage` fsyncs the file + parent directory on every write/move (APP-C1), so this ordering
is durable across a hard crash, not just correct in program order.

## After a restore: self-healing

1. Restore Postgres (PITR) and the `files_root` tree (per the restore rule above).
2. Run **`doktok-worker repair`** (APP-C2): it walks active documents and
   - re-queues the feature ledger to rebuild derived artifacts when the original survives but derived
     ones are missing (the reconciler then backfills them idempotently),
   - reports documents whose **original is missing** as *unrecoverable* (never auto-deleted),
   - with `--check-hashes` (APP-D2), verifies each original's sha256 against the row (the DB is the
     manifest) and reports *corrupted* originals.
   `--dry-run` reports without changing anything; a non-zero exit flags unrecoverable/corrupted docs.
3. The reconciler (already running) drives every active document back to "all features done".

Most mid-pipeline crash states already self-heal: stale-job recovery re-queues stranded ingests,
reconciler lease reclamation re-queues `running` features, the FK cascade + active-sha unique index
prevent orphan/duplicate rows, and feature processors are idempotent (delete-then-rewrite).

## Consistent snapshots: quiesce

`doktok-worker quiesce` (APP-C3) sets maintenance mode; the running worker then starts **no new
ingestion or reconcile work** while in-flight work finishes, so a backup can capture a still DB +
`files_root` pair. `quiesce --off` resumes. It is optional — backups are crash-consistent without it
(the ordering invariant + repair handle the gaps); quiesce just narrows the window.

Typical backup sequence: `quiesce` → snapshot Postgres + `files_root` → `quiesce --off`.

## App-level RPO (in-flight ingestion at failure)

- File still in the ingest folder, no job yet → zero loss (re-scanned).
- Job created, original in the workdir, mid-extract/OCR → stale-job recovery reprocesses it on
  restart; no loss provided the original was durably written (APP-C1).
- Feature row `running` when killed → lease reclamation re-queues it; idempotent reprocess. No loss.
- Net: with durable writes, the app adds no RPO of its own beyond the Postgres WAL RPO; derived data
  is never an RPO concern because it is re-derivable.

## Related

- M12 epic #366 (Backup, Recovery & Disaster Readiness)
- ADR-0009 (feature reconciler), ADR-0012 (FK cascade + activation ordering), ADR-0015 (staged
  pipeline)
- `docs/operations/security-runbook.md`, `docs/operations/deployment-trigkey-n95.md`
