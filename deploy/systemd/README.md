# Backup scheduling via systemd timers (M12 DEVOPS-B3)

Host-level timers run the backup scripts on a cadence, **resource-capped** so they never starve OCR
or ingestion on the 8 GB box, with `OnFailure=` alerting. Host timers (not a compose sidecar) keep
RAM free except for the few seconds a job runs. Review-grade: install these on the box.

Secrets/config come from `/etc/doktok/backup.env` (root-owned, `chmod 600`):
```
DOKTOK_DEPLOY_MODE=compose          # compose (containerized box) or host (dev/test)
DOKTOK_BACKUP_DIR=/var/lib/doktok/backups
DOKTOK_FILES_ROOT=/var/lib/doktok/files
DOKTOK_RESTIC_PASSWORD=...
DOKTOK_PGBACKREST_CIPHER_PASS=...
DOKTOK_AZURE_ACCOUNT=...   DOKTOK_AZURE_CONTAINER=...   DOKTOK_AZURE_SAS=...
```
(The restic/pgBackRest passphrases must ALSO be stored off the box - a repo is useless without them.)

## Install (shipped units)

The core timers ship as real unit files in this directory and are installed by
`deploy/install-systemd.sh` (run as root on the box, after writing `/etc/doktok/backup.env`):

```
sudo ./deploy/install-systemd.sh
systemctl list-timers 'doktok-*'
```

It installs `doktok-backup-diff.timer` (hourly), `doktok-backup-full.timer` (weekly), and
`doktok-pg-wal-freshness.timer` (every minute). All run from `WorkingDirectory=/opt/doktok` and read
`/etc/doktok/backup.env`, so they honour `DOKTOK_DEPLOY_MODE`: in **compose** mode the backup units
call the mode-aware `deploy/backup.sh` (files via the `backup-runner` container + pg via
`docker compose exec db pgbackrest`); in **host** mode the same script runs the host backup tools.
They run as **root** because compose mode needs Docker access and the status sentinels are
root-owned. The azure-sync / check-backup / restore-drill / ollama-autostop units are documented
below and installed the same way (copy the example unit blocks into `/etc/systemd/system/`).

### pg WAL-freshness (DRP)

The pg leg's RPO is ~60s (continuous WAL archiving), but base backups only run hourly/weekly, so the
pg sentinel would flap "stale" between them. `doktok-pg-wal-freshness.timer` runs
`deploy/pg-wal-freshness.sh` every minute: it stamps the pg sentinel's `last_run_at` to the last
archived WAL time (the real recovery point) and records `wal_lag_s`, preserving the base backup's
`size`/`backup_id` metrics. `archive_timeout=60` on the db (set in `docker-compose.prod.yml`) forces
a WAL switch each minute so an idle DB still keeps the recovery point fresh.

Shared `[Service]` shape of the shipped backup units (`doktok-backup-diff`/`doktok-backup-full`):
`Type=oneshot`, `WorkingDirectory=/opt/doktok`, `EnvironmentFile=/etc/doktok/backup.env`, `Nice=10`,
`IOSchedulingClass=idle`, `CPUQuota=100%`, `MemoryMax=512M`, and `After=/Requires=docker.service`.
They run as **root** (no `User=`) because compose mode needs Docker access and the status sentinels
are written `0644` by root. The per-minute `doktok-pg-wal-freshness` service is lighter
(`Nice=10`, `MemoryMax=128M`, no `CPUQuota`/IO cap - it only runs a single `psql` query). The
example units further below add a `User=doktok` / `OnFailure=doktok-backup-alert@%n.service` pattern
for the host-mode / optional timers; the shipped compose-mode units omit both.

## Cadence
| Timer | Schedule | Runs |
|---|---|---|
| doktok-backup-diff | hourly | `deploy/backup.sh diff` (files + pg differential, mode-aware) |
| doktok-backup-full | weekly (Sun 03:00) | `deploy/backup.sh full` (files + pg full, mode-aware) |
| doktok-pg-wal-freshness | every 1 min | `deploy/pg-wal-freshness.sh` (stamps the pg WAL recovery point) |
| doktok-backup-files | every 15 min | `deploy/backup-files.sh` (host-mode only; compose uses backup-diff) |
| doktok-backup-pg | hourly (diff) + weekly full | `deploy/backup-pg.sh diff` / `full` (host-mode only) |
| doktok-backup-pg-logical | weekly | `deploy/backup-pg-logical.sh` (portable logical safety-net) |
| doktok-azure-sync | hourly | `deploy/azure-sync.sh` |
| doktok-check-backup | every 30 min | `deploy/check-backup-freshness.sh` |
| doktok-restore-drill | monthly | `deploy/restore-drill.sh` |
| doktok-ollama-autostop | every 2 min | `deploy/ollama-autostop.sh` (stop/start the Ollama container by need, M16 #374) |

## Example unit (files snapshot) — the rest follow the same shape

`/etc/systemd/system/doktok-backup-files.service`
```ini
[Unit]
Description=DokTok files_root backup (restic)
[Service]
Type=oneshot
User=doktok
WorkingDirectory=/opt/doktok
EnvironmentFile=/etc/doktok/backup.env
ExecStart=/opt/doktok/deploy/backup-files.sh
Nice=10
IOSchedulingClass=idle
CPUQuota=100%
MemoryMax=512M
OnFailure=doktok-backup-alert@%n.service
```
`/etc/systemd/system/doktok-backup-files.timer`
```ini
[Unit]
Description=Run the DokTok files_root backup every 15 minutes
[Timer]
OnCalendar=*:0/15
Persistent=true
[Install]
WantedBy=timers.target
```

Other services swap `Description`/`ExecStart`:
- `doktok-backup-pg.service` -> `ExecStart=/opt/doktok/deploy/backup-pg.sh diff` (+ a `-full` variant `OnCalendar=Sun 03:00`)
- `doktok-azure-sync.service` -> `ExecStart=/opt/doktok/deploy/azure-sync.sh` (timer `OnCalendar=hourly`)
- `doktok-check-backup.service` -> `ExecStart=/opt/doktok/deploy/check-backup-freshness.sh` (timer `OnCalendar=*:0/30`)
- `doktok-restore-drill.service` -> `ExecStart=/opt/doktok/deploy/restore-drill.sh` (timer `OnCalendar=monthly`)
- `doktok-ollama-autostop.service` -> `ExecStart=/opt/doktok/deploy/ollama-autostop.sh` (timer
  `OnCalendar=*:0/2`). Needs Docker access (run as a user in the `docker` group, not the sandboxed
  `doktok` user) since it runs `docker compose start/stop ollama`.

`doktok-backup-alert@.service` is a oneshot that sends a notification (email/webhook) for any failed
backup unit, e.g. `ExecStart=/opt/doktok/deploy/notify.sh "%i failed"`.

Prune/expire runs inside the backup scripts (restic forget --prune; pgBackRest retention) - keep that
on the scheduled path, not on the offsite immutable copy.
