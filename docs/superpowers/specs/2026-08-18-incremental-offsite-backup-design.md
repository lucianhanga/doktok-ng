# Incremental offsite backup — design

Date: 2026-08-18
Status: Approved design (pending spec review); supersedes the tarball/GFS transport of `deploy/azure-sync.sh` (#766) once implemented. Will be recorded as an ADR in the implementation PR.

## Context

Measured incident (2026-08-18, dev box): a misconfigured dev cron (15-min `TYPE=full`) grew the local
pgBackRest repo to 9.3 GB against a 29 MB database; Azure held ~14 GB across the two containers, of
which 4.65 GB was undeletable (WORM policy window). The local cadence is fixed separately
(incr/15-min, full weekly, 7-day dev retention via `PGBACKREST_REPO1_RETENTION_FULL`).

The structural problem this design addresses: the offsite transport tars the **entire** local repo
per sync and keeps up to 7 daily + 16 GFS full copies per leg in Azure. Storage grows with
`corpus × retention classes` instead of `corpus + churn`:

| corpus scale | local repos (incremental) | Azure, tarball transport (status quo) | Azure, this design |
|---|---|---|---|
| 1× (today, 2.4 GB files / 29 MB db) | ~1.2 GB | ~10–14 GB | ~1.2 GB |
| 100× | ~50–70 GB | ~1–2 TB | ~50–70 GB |
| 1000× | ~0.4–1 TB | ~10–25 TB | ~0.4–1.1 TB |

Sizing assumes a text-heavy corpus (~5:1 restic compression, observed 2026-08-18: 2.353 GiB →
412 MB). A scan-heavy corpus shifts file-leg estimates ~4× up; the architecture is unaffected.

## Decisions already taken (with the user, 2026-08-18)

1. **Offsite RPO = 1 hour** → hourly sync cadence, dev crontab and prod `doktok-azure-sync.timer`.
   Local RPOs unchanged: pg ~60 s (WAL `archive_timeout=60`), files 15 min (restic cron).
2. **Transport = restic end-to-end** ("option A"). pgBackRest native Azure `repo2` ("option B") is
   explicitly shelved; revisit only if ~1-min *offsite* pg currency is ever required.
3. **WORM:** drop the time-based immutability policies on the backup containers. Ransomware
   resistance moves to the access layer: blob soft-delete (30 days) + a sync SAS **without** delete
   permission; prune runs under a separate delete-capable credential stored host-only.

## Goals

- Offsite uploads and storage become incremental: only new chunks cross the wire; Azure holds ~1×
  the compressed corpus plus churn.
- GFS retention (daily 7 / weekly 4 / monthly 12 / yearly 1) becomes snapshot metadata over shared
  chunks — no duplicate bytes.
- The DRP sentinel/history contract (`backups/status/offsite.json`, `history.jsonl`) is preserved
  byte-compatibly: the backend DRP panel, watchdog, and drills keep working unchanged.
- Restore runbook shape is preserved: offsite content is fetched, then the existing
  `restore-import.sh` / pgBackRest two-stage path applies.

## Non-goals

- Changing local backup cadences, retention, or the pgBackRest/restic local setups (just fixed).
- RTO improvements at scale (restore of ~1 TB class data takes hours; documented, out of scope).
- pgBackRest `repo1-type=azure` (option B), multi-region, or a second offsite target.

## Architecture

Two restic repos on Azure Blob, created once by provisioning:

- `azure:<DOKTOK_AZURE_CONTAINER>:/files` — offsite copy of the files repo
- `azure:<DOKTOK_AZURE_CONTAINER>:/pg` — offsite copy of the pgBackRest repo

**Files leg (hourly):** `restic backup` of the files tree straight into the Azure files repo (same
source ⇒ same chunk dedup; only churn crosses the wire). In compose mode the runner reuses
`backup-files.sh`'s virtiofs staging (`DOKTOK_FILES_STAGE_SRC` → `/data/files`, #745). Deliberately
NOT `restic copy` from the local repo: its `--from-repo` auth proved unreliable on restic 0.14
(spike 2026-08-18); direct backup keeps every leg on single-repo auth. Revisit `copy` as an
optimization at scale (it would spare re-reading the tree hourly).

**Pg leg (hourly):** `restic backup "$DOKTOK_BACKUP_DIR/pg"` into the Azure pg repo, excluding
`log/` and `lock/`. pgBackRest repo files are write-once, so dedup is perfect at file/chunk
granularity: each run uploads only new WAL segments and new backup files. (The pg repo password is
the same `DOKTOK_RESTIC_PASSWORD`; same data, same trust domain.)

**Retention (hourly, after the copies):** `restic forget --prune` on both Azure repos with
`--keep-daily 7 --keep-weekly 4 --keep-monthly 12 --keep-yearly 1`, grouped by host+paths. Local
repos keep their existing policies.

**`deploy/azure-sync.sh`** is rewritten to a thin orchestrator: copy files → backup pg repo →
forget/prune both → write the `offsite.json` sentinel (`leg/ok/last_run_at/detail`) and append the
hash-chained `history.jsonl` event, via the existing `lib.sh` helpers (`write_status`, `log_event`,
`fail_sync` semantics preserved: any failure ⇒ `ok:false` + FAILED on the DRP panel).

**Access model:**
- `DOKTOK_AZURE_SAS` — read/write/list, **no delete**, used for copy/backup. Stored in `.env` as
  today; compromise of it cannot destroy backup history.
- `DOKTOK_AZURE_SAS_PRUNE` — delete-capable, used only by the forget/prune step. Host-only (prod:
  `/etc/doktok/backup.env`; dev: `.env`, documented as sensitive). Never read by the app.
- The script maps these onto restic's Azure backend env at invocation time
  (`AZURE_ACCOUNT_NAME` ← `DOKTOK_AZURE_ACCOUNT`, `AZURE_ACCOUNT_SAS` ← the step-appropriate SAS;
  the prune SAS is exported only around the forget/prune commands).
- Blob **soft-delete, 30 days**, is enabled at the **storage account** level (it is an account
  property, covering both containers); the per-container time-based **immutability policies are
  removed** as part of migration — they conflict with pruning (incident of 2026-08-18).

**Scheduling:** hourly. Dev: crontab line changes from `47 3 * * *` to `7 * * * *`. Prod:
`doktok-azure-sync.timer` `OnCalendar=hourly`. Hourly uploads are tiny (incremental), so the cadence
is affordable; the 1 h offsite RPO holds for both legs.

## Data flow / failure handling

- Happy path: local backups run as today (15-min files+incr pg, weekly full, per-minute WAL stamp);
  the hourly sync ships the delta; sentinel `ok:true`, detail carries both repo fps + snapshot ids.
- Sync failure at any step ⇒ `fail_sync` (sentinel `ok:false`, DRP shows FAILED) — unchanged.
- Stale restic locks on the Azure repo are swept only when older than 2× the sync interval and no
  matching live process (restic locks are host-pid-bound; a crashed run's lock is safe to remove
  after the interval).
- Postgres WAL currency is still monitored by the existing per-minute `pg-wal-freshness` stamp; the
  offsite leg inherits whatever the local repo holds — no new monitoring needed.

## Restore

- Files: `restic -r azure:…:/files restore latest --target …` (replaces the tarball download in
  `azure-fetch.sh`), then rebuild a local staging restic repo from the tree (`init` + `backup`) so
  the existing `restore-files.sh`/`restore-import.sh` repo contract is unchanged.
- Pg: `restic -r azure:…:/pg restore latest --target <staging>` → point pgBackRest at the restored
  repo → existing `restore-pg.sh` (PITR) path. Two-stage, as today.
- `azure-fetch.sh` keeps its interface (fetch a set into a staging dir) but becomes a restic restore
  instead of tarball download+extract; `TS=` selects a snapshot timestamp instead of a blob name.

## Migration

1. Provision: enable soft-delete (30 d) on both containers; remove the time-based immutability
   policies; `restic init` both repo prefixes. All scripted in `azure-provision.sh`.
2. First sync seeds the Azure repos (one full upload of the current local state: ~0.4–0.8 GB at
   1× scale — the legacy tarball sets are *not* migrated).
3. Legacy tarball blobs are left to age out: short container by keep-count churn, LTS by policy
   expiry (the 2026-07-28 sets become deletable ~2026-08-27); a cleanup note goes into the
   backup-and-recovery runbook.
4. Rollback: keep the old `azure-sync.sh` tarball path behind `DOKTOK_OFFSITE_TRANSPORT=tarball`
   for one release, defaulting to `restic`. (Escape hatch only; no new logic.)

## Testing

- Backend text-contract tests that parse `deploy/*.sh` are updated to the new script shape
  (they already exist for the backup scripts; extend for the retention/access invariants:
  sync SAS must not contain delete permission in docs, prune step must reference the prune SAS).
- `drp-selftest` gains an offsite round-trip leg against a throwaway Azure prefix:
  seed → sync → forget/prune → restore → compare hashes. Live-config like the other eval harnesses;
  skipped when Azure env is absent.
- A restore-drill variant restores **from** the Azure repos (not just the local ones) on demand.
- CI: no Azure access ⇒ unit/contract tests only; live path covered by drills, matching how the
  current backup code is tested.

## Risks

- **restic↔Azure throttling** at 1000×: mitigate with Cool tier on the container and restic's
  `-o azure.connections` tuning; monitor sync duration in the sentinel detail.
- **Soft-delete is not WORM**: a delete-capable-credential compromise can destroy history (30-day
  soft-delete still allows recovery within the window). Accepted by the user 2026-08-18 in exchange
  for operability; re-evaluate if compliance requirements change.
- **Two credentials to provision** instead of one: handled by `azure-provision.sh` + runbook.

## Outcome (success criteria)

- Hourly sync completes in minutes at 1× scale; upload volume ≈ daily churn, not repo size.
- Azure storage ≈ local repo size (± retention overlap), verified by measuring both after 30 days.
- DRP panel shows offsite OK with the same sentinel contract; drills restore from Azure repos.
- At 100× the Azure bill stays in the tens of GB, not terabytes.
