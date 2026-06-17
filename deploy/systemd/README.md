# Backup scheduling via systemd timers (M12 DEVOPS-B3)

Host-level timers run the backup scripts on a cadence, **resource-capped** so they never starve OCR
or ingestion on the 8 GB box, with `OnFailure=` alerting. Host timers (not a compose sidecar) keep
RAM free except for the few seconds a job runs. Review-grade: install these on the box.

Secrets/config come from `/etc/doktok/backup.env` (root-owned, `chmod 600`):
```
DOKTOK_BACKUP_DIR=/var/lib/doktok/backups
DOKTOK_FILES_ROOT=/var/lib/doktok/files
DOKTOK_RESTIC_PASSWORD=...
DOKTOK_PGBACKREST_CIPHER_PASS=...
DOKTOK_AZURE_ACCOUNT=...   DOKTOK_AZURE_CONTAINER=...   DOKTOK_AZURE_SAS=...
```
(The restic/pgBackRest passphrases must ALSO be stored off the box - a repo is useless without them.)

Install: copy the unit blocks below into `/etc/systemd/system/`, set `WorkingDirectory` to the repo
checkout, then `systemctl daemon-reload && systemctl enable --now doktok-backup-files.timer …`.

Shared `[Service]` hardening (every backup service): `Nice=10`, `IOSchedulingClass=idle`,
`CPUQuota=100%`, `MemoryMax=512M`, `EnvironmentFile=/etc/doktok/backup.env`, `Type=oneshot`,
`User=doktok`, `OnFailure=doktok-backup-alert@%n.service`.

## Cadence
| Timer | Schedule | Runs |
|---|---|---|
| doktok-backup-files | every 15 min | `deploy/backup-files.sh` |
| doktok-backup-pg | hourly (diff) + weekly full | `deploy/backup-pg.sh diff` / `full` |
| doktok-backup-pg-logical | weekly | `deploy/backup-pg-logical.sh` (portable logical safety-net) |
| doktok-azure-sync | hourly | `deploy/azure-sync.sh` |
| doktok-check-backup | every 30 min | `deploy/check-backup-freshness.sh` |
| doktok-restore-drill | monthly | `deploy/restore-drill.sh` |

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

`doktok-backup-alert@.service` is a oneshot that sends a notification (email/webhook) for any failed
backup unit, e.g. `ExecStart=/opt/doktok/deploy/notify.sh "%i failed"`.

Prune/expire runs inside the backup scripts (restic forget --prune; pgBackRest retention) - keep that
on the scheduled path, not on the offsite immutable copy.
