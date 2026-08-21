# Rethink offsite pg transport: whole-repo tarballs don't scale

## Problem

`deploy/azure-sync.sh` ships offsite DR copies by tarring the **entire** local pgBackRest repo into
one `.tar.gz` per sync and uploading it whenever the content fingerprint changes:

- Upload cost is **O(repo size) per sync**, not O(changes). With the repo at ~9 GB (observed on the
  dev box, 2026-08-18) a single daily sync uploaded ~9 GB; at 10× data scale this becomes untenable.
- Storage is duplicated: the full local repo plus N full-repo copies across the Azure GFS classes
  (hourly 24 / daily 7 / weekly 4 / monthly 11 / yearly 1 keep counts).
- The files leg does not have this problem: restic is incremental by design (content-defined dedup).

Observed trigger: dev box accumulated 448 full pg backups in 25 days (15-min `TYPE=full` cron +
30-day retention), repo grew to 9.3 GB while the actual database is 29 MB. Dev-side cadence is now
fixed (incr every 15 min, full weekly, 7-day dev retention via `PGBACKREST_REPO1_RETENTION_FULL` in
`docker-compose.dev.yml`), but the transport cost scales with repo size regardless of cadence.

## Options

### 1. pgBackRest native Azure repo as `repo2` (proper end-state)

pgBackRest supports Azure Blob natively (`repo1-type=azure` + account/SAS or key) and multi-repo
configurations are first-class: keep `repo1` local for fast restores, add `repo2` in Azure;
`archive-push` WALs and run incremental backups to both. Offsite becomes incremental-forever with
per-backup uploads only — no tarballs, no duplicated full copies.

Design work needed: per-repo cipher keys, repo2 retention mapping (today's GFS classes → pgBackRest
retention on repo2), how WORM/immutability policies interact with pgBackRest expire on Azure,
changes to the sentinel/history contract (`status/offsite.json`), and restore-drill adjustments.

### 2. restic the pg repo directory (cheap interim, reuses existing infra)

The pgBackRest repo is **write-once** (backup + WAL files are never modified in place), so restic
deduplicates it perfectly at file granularity — each offsite run uploads only newly created repo
files. Single offsite tool (restic), GFS via restic retention policy, WORM story unchanged.

Trade-off: restore is two-stage (restic restore the repo, then `pgbackrest restore` from it) — but
the current `azure-fetch.sh` → `restore-import.sh` flow already works exactly this way, so the
runbook shape survives.

### 3. Keep tarballs, cut frequency (minimal)

e.g. weekly pg tarball only. Offsite pg RPO degrades from ~1 h to a week — conflicts with the stated
offsite target RPO (1 h). Not recommended beyond a stopgap.

## Recommendation

Option 2 as the interim fix (small, reuses restic, keeps the runbook shape), option 1 as the ADR'd
target architecture. Decide in an ADR before implementation; the offsite sentinel/history contract
(`deploy/lib.sh`, `backups/status/*.json`) and the DRP panel's expectations must be updated
together with whatever is chosen.

## References

- `deploy/azure-sync.sh` (current tarball transport, GFS classes, fingerprint dedup)
- `deploy/pgbackrest/pgbackrest.conf` (retention, cipher)
- `docker-compose.dev.yml` (dev retention override added 2026-08-18)
- ADR-0020 (hybrid deployment topology), #766 (offsite leg v3)
