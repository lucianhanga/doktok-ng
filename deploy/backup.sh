#!/usr/bin/env bash
#
# Orchestrate a full local-first backup (M12 #363): files_root (restic) + Postgres (pgBackRest) into
# $DOKTOK_BACKUP_DIR. Called by deploy.yml before a deploy and by the systemd timers. Offsite sync to
# Azure is a separate step (azure-sync.sh); the weekly logical safety-net is backup-pg-logical.sh.
#
# Supersedes the old pg_dump+tar snapshot (DEVOPS-6): the engine gives low-RPO PITR + dedup snapshots
# and writes the freshness sentinels the DRP panel reads. Arg 1: pg backup type (full|diff|incr).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

warn "backups are secret-bearing (encrypted) - store the repo + keys off the box"

type="${1:-incr}"
mode="${DOKTOK_DEPLOY_MODE:-host}"

if [ "$mode" = "compose" ]; then
    # Containerized (staging/prod): same scripts, run where the tools + data are (M12 #377). Files
    # run in the backup-runner (restic + mounts); pg runs inside the db container (pgbackrest lives
    # there), then the runner records the pg sentinel into the shared backup dir.
    compose=(docker compose -f docker-compose.prod.yml --env-file .env.production)
    "${compose[@]}" run --rm backup-runner deploy/backup-files.sh
    "${compose[@]}" exec -T db pgbackrest --stanza=doktok backup --type="$type"
    "${compose[@]}" run --rm backup-runner deploy/write-status.sh pg true "pgbackrest $type"
else
    # Host (dev/test): tools installed on the host, host file paths.
    ./deploy/backup-files.sh
    ./deploy/backup-pg.sh "$type"
fi
ok "backup complete (mode=$mode) -> ${BACKUP_DIR}"
