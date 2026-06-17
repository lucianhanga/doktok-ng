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
```

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

## Secrets

`DOKTOK_RESTIC_PASSWORD` and `DOKTOK_PGBACKREST_CIPHER_PASS` encrypt the repos - **store them off the
box** (a repo is useless without its key, and an SSD failure would take both). Azure credentials
(`DOKTOK_AZURE_SAS` / key) are write secrets. Names only in env examples; see the security runbook.
