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
./deploy/backup-files.sh
./deploy/backup-pg.sh "${1:-incr}"
ok "backup complete -> ${BACKUP_DIR}"
