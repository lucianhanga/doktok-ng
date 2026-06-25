#!/usr/bin/env bash
#
# Write a DRP freshness sentinel (M12 #377): deploy/write-status.sh <leg> <true|false> [detail].
# Used in compose mode to record the pg leg after `docker compose exec db pgbackrest backup` (the
# backup ran in the db container, but the sentinel must land in the shared $DOKTOK_BACKUP_DIR/status,
# which this writes from the backup-runner that has the backup dir mounted).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh
write_status "$1" "${2:-true}" "${3:-}"
ok "wrote status sentinel: $1 (${2:-true})"
