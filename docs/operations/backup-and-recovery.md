# Backup & recovery (local-first, Azure offsite)

The running, low-RPO backup design for the hybrid deployment (M12). **Local-first:** every backup is
staged into a local repository folder first, then a thin step ships it offsite to Azure Blob - so the
whole engine works and is testable with no cloud account. See also
[backup-restore-consistency.md](backup-restore-consistency.md) (the app-level guarantees) and the
M12 epic (#366).

## Targets

| Data | RPO | Mechanism |
|---|---|---|
| Postgres | ~1 min | pgBackRest: continuous WAL archiving + base/diff backups (PITR) |
| files_root | ~15 min | restic dedup snapshots (encrypted) |
| Offsite (both) | ~1 h | `azure-sync.sh` to Azure Blob (immutability + versioning account-side) |

RTO ~2-4 h to a working stack (restore is bandwidth-bound from Azure; keep recent backups in a
Hot/Cool tier, not Archive, so rehydration latency doesn't blow RTO).

## Layout

```
$DOKTOK_BACKUP_DIR/            # default ./backups (gitignored); on the box a real volume
  files/                      # restic repo (files_root: dedup + AES-256)
  pg/                         # pgBackRest repo (base + WAL, aes-256-cbc)
  pgbackrest.conf             # generated from env (chmod 600)
  status/                     # per-leg freshness sentinels (JSON, chmod 0644) - see below
```

## Freshness sentinels (the DRP source of truth)

Each backup leg writes one JSON file under `$DOKTOK_BACKUP_DIR/status/<leg>.json` (written by
`write_status` in `deploy/lib.sh`, atomically via a temp file + rename, `chmod 0644` so the backend -
a different uid in compose - can read it). The sentinels live **outside** Postgres so a DB restore
can't roll backup status back. They feed `deploy/check-backup-freshness.sh`, the `/metrics` gauges,
and the Settings -> DRP panel.

Schema (common fields plus optional per-leg metrics captured during the backup, M12 #380):

```json
{
  "leg": "files",
  "ok": true,
  "last_run_at": "2026-06-25T20:00:00Z",
  "detail": "restic snapshot",
  "size": "662.7 MiB",
  "file_count": 287,
  "backup_id": "a1b2c3d4"
}
```

- `last_run_at` is normally the run time, but for the **pg** leg it is the **recovery point** - the
  last archived WAL time, stamped each minute by `deploy/pg-wal-freshness.sh` via the
  `WRITE_STATUS_TS` override (see below).
- **files** leg (restic, parsed from `restic backup` output in `deploy/backup-files.sh`): `size`,
  `file_count`, `backup_id` (snapshot id).
- **pg** leg (pgBackRest, parsed from `pgbackrest info --output=json` by the `pg_backup_extra` helper
  in `deploy/lib.sh`): `size` (database size), `backup_id` (backup label), plus `wal_lag_s` (seconds
  since the last archived WAL) added by `pg-wal-freshness.sh`.

## Scripts (`deploy/`)

| Script | Purpose |
|---|---|
| `backup-files.sh` | restic snapshot of files_root -> local repo (+ prune/retention) |
| `restore-files.sh <target> [snap]` | restore files_root from the local repo |
| `backup-pg.sh [full\|diff\|incr]` | pgBackRest backup -> local repo |
| `restore-pg.sh ["<time>"]` | pgBackRest restore / PITR into `$DOKTOK_PGDATA` |
| `azure-sync.sh [--dry-run]` | push the local repo to Azure Blob (offsite leg) |
| `test-pitr.sh` | self-contained proof that folder-based WAL+base+PITR works (throwaway containers) |
| `backup.sh` / `restore.sh` | the dependency-light pre-deploy snapshot (pg_dump + tar), called by deploy.yml |

`backup.sh` (M11/DEVOPS-6) is the simple, Docker-only **pre-deploy snapshot**; the restic/pgBackRest
scripts above are the scheduled **low-RPO running backups**.

## What is verified locally vs box-side

- **Verified here:** files backup -> restore round-trips identically and the restic repo is
  encrypted; the folder-based WAL+base+PITR mechanism (`test-pitr.sh`: restore to a target time
  contains only pre-target data). The `repair`/`verify-restore` reconciler runs against the live DB.
- **Box-side (needs the host / Azure account):** wiring Postgres `archive_command=pgbackrest
  --stanza=doktok archive-push %p` into the db container (DB-A1); systemd timers + resource caps;
  Azure account, container immutability/versioning/lifecycle; the firewall. These are M12 infra
  tickets to apply on the N95.

## Back up (scheduled on the box)

```bash
# files (every ~15 min) and Postgres (base/diff per schedule); then offsite
DOKTOK_RESTIC_PASSWORD=... ./deploy/backup-files.sh
DOKTOK_PGDATA=/var/lib/postgresql/data DOKTOK_PGBACKREST_CIPHER_PASS=... ./deploy/backup-pg.sh diff
DOKTOK_AZURE_ACCOUNT=... DOKTOK_AZURE_CONTAINER=... DOKTOK_AZURE_SAS=... ./deploy/azure-sync.sh
```

Wrap with the quiesce hook for a still snapshot: `doktok-worker quiesce` -> back up -> `quiesce --off`.

## Restore

1. (If offsite) pull the repo from Azure into `$DOKTOK_BACKUP_DIR` (reverse of `azure-sync.sh`).
2. Stop the stack. Restore Postgres to a point at or before the latest files snapshot:
   `DOKTOK_PGDATA=... DOKTOK_PGBACKREST_CIPHER_PASS=... ./deploy/restore-pg.sh "2026-06-16 20:00:00+00"`
3. Restore files_root (>= the DB restore point): `./deploy/restore-files.sh /var/lib/doktok/files`
4. Start the stack, then `doktok-worker repair` to reconcile DB <-> files and re-queue any
   re-derivable gaps (the reconciler backfills derived artifacts).

## Box-side: scheduling, monitoring, offsite

- **WAL archiving:** the prod `db` image (`deploy/docker/db.Dockerfile`) bundles pgBackRest; the
  compose `db` service sets `archive_command=pgbackrest --stanza=doktok archive-push %p` so WAL ships
  continuously into the local repo (`deploy/pgbackrest/pgbackrest.conf`, cipher pass from env). It
  also sets `archive_timeout=60` so an idle DB still switches and ships one WAL segment each minute,
  keeping the recovery point fresh.
- **Scheduling:** host systemd timers run the scripts capped (`Nice`/`idle` IO/`MemoryMax`) so they
  never starve OCR/ingest. The shipped units are installed by `deploy/install-systemd.sh` (run as
  root, after writing `/etc/doktok/backup.env`): `doktok-backup-diff` (hourly -> `backup.sh diff`),
  `doktok-backup-full` (weekly Sun 03:00 -> `backup.sh full`), and `doktok-pg-wal-freshness` (every
  minute -> `pg-wal-freshness.sh`). The units are **mode-aware** via `DOKTOK_DEPLOY_MODE` in
  `backup.env`: in `compose` mode they drive the containerized tools; in `host` mode they run the host
  tools. The azure-sync / check-backup / restore-drill / ollama-autostop timers are documented as
  example units in `deploy/systemd/README.md`.
- **pg WAL-freshness:** `deploy/pg-wal-freshness.sh` (every minute) stamps the pg sentinel's
  `last_run_at` to the last archived WAL time (the real recovery point) and records `wal_lag_s`,
  **preserving** the base backup's `size`/`backup_id`. Without it the pg leg would flap "stale"
  between the hourly/weekly base backups even though continuous WAL archiving holds the ~60s RPO.
- **Freshness/monitoring:** each script writes a per-leg sentinel `$DOKTOK_BACKUP_DIR/status/<leg>.json`
  (outside Postgres, so a DB restore can't roll status back) carrying the captured size/file-count/id
  metrics (see [Freshness sentinels](#freshness-sentinels-the-drp-source-of-truth) above).
  `deploy/check-backup-freshness.sh` alerts on stale/failed legs; the same sentinels feed the
  `/metrics` gauges and the Settings -> DRP panel; `.github/workflows/backup-watchdog.yml` is an
  independent off-box watchdog.
- **Restore drills:** `deploy/restore-drill.sh` restores the latest files snapshot + runs the
  Postgres PITR proof into throwaway locations and records the result (monthly timer).
- **Azure offsite:** provision once with `deploy/azure-provision.sh` (account + container + versioning
  + time-based immutability); `azure-sync.sh` pushes the local repo. Keep recent backups Hot/Cool.

## Settings -> DRP panel

The backend reads the sentinels and exposes them at `GET /drp` (`apps/backend/doktok_api/routers/settings.py`,
`DrpStatus`/`BackupLegStatus` in `contracts/doktok_contracts/schemas.py`). For each leg the backend
**derives a state** from the sentinel: `ok` is `false` -> **failed**; else age `> 3 x` the leg's
target RPO -> **stale**; else **ok**; a missing or never-run sentinel -> **unknown**.

The Settings -> **DRP** tab (`apps/ui/src/SettingsPanel.tsx` `DrpSection`, polled every 45s) renders
one status **card** per leg - **Files (restic)**, **Postgres (pgBackRest)**, **Offsite (Azure)**, and
**Last restore drill**. Each card shows:

- a colour-coded state **badge** - green `ok`, amber `stale`, red `failed`, grey `unknown` (the badge
  text is the state word itself; colour comes from CSS in `apps/ui/src/styles.css`);
- the last-run **age** (for the pg leg this is the WAL recovery point, not the base-backup time);
- the captured **metrics** as a small list: `Size`, `Files` (files leg), `ID` (restic snapshot id /
  pgBackRest label, truncated with the full id in a tooltip), and the leg's `Target RPO`.

A **WAL shipping lag** note under the grid surfaces `wal_lag_seconds` (from the pg sentinel's
`wal_lag_s`), and a configuration block shows the repo location, deploy mode, and Azure container.

## Gotchas

- **Run pgBackRest in the db container as the `postgres` user.** `deploy/backup.sh` runs
  `docker compose exec -u postgres -T db pgbackrest ...`, **not** as root. `exec` defaults to root,
  which rewrites the repo's `archive.info` as root-owned `0640`; the WAL `archive_command` then runs
  as the postgres **server** uid (999) and can no longer read it, so all WAL archiving fails with
  `WAL segment ... not archived before the 60000ms timeout`. If a manual `pgbackrest` run as root has
  already broken it on a box, fix the ownership:
  ```bash
  sudo chown -R 999:999 $DOKTOK_BACKUP_DIR/pg
  ```
- **pg leg shows "stale" but WAL is fine.** The pg sentinel's freshness comes from
  `pg-wal-freshness.sh` (last archived WAL), not the base backup. If the leg ages, check that the
  `doktok-pg-wal-freshness.timer` is active (`systemctl list-timers 'doktok-*'`) and that
  `archive_timeout=60` is set on the db, so an idle DB still ships a segment each minute.

## Secrets

`DOKTOK_RESTIC_PASSWORD` and `DOKTOK_PGBACKREST_CIPHER_PASS` encrypt the repos - **store them off the
box** (a repo is useless without its key, and an SSD failure would take both). Azure credentials
(`DOKTOK_AZURE_SAS` / key) are write secrets. Names only in env examples; see the security runbook.
