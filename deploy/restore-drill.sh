#!/usr/bin/env bash
#
# Scheduled restore drill (M12 DEVOPS-D3): prove the backups actually restore, into a throwaway
# location, and record the result in the drill sentinel for the DRP panel + freshness check. An
# untested backup is not a backup. Run monthly via a systemd timer; alert on failure.
#
# It (1) restores the latest files snapshot into a temp dir and checks it is non-empty, and (2) runs
# the self-contained Postgres PITR proof (test-pitr.sh). It does NOT touch production data.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

trap 'write_status drill false "drill failed"; err "restore drill FAILED"; exit 1' ERR

tmp="$(mktemp -d)"
cleanup() { rm -rf "$tmp"; }
trap 'cleanup' EXIT

echo "=== drill 1/2: restore latest files snapshot ==="
DOKTOK_BACKUP_DIR="$BACKUP_DIR" ./deploy/restore-files.sh "$tmp/files" >/dev/null
restored_count="$(find "$tmp/files" -type f | wc -l | tr -d ' ')"
[ "$restored_count" -gt 0 ] || {
    err "files restore produced no files"
    false
}
ok "files restore OK (${restored_count} files)"

echo "=== drill 2/2: Postgres PITR proof ==="
./deploy/test-pitr.sh >/dev/null
ok "Postgres PITR proof OK"

write_status drill true "files=${restored_count} + pitr ok"
ok "restore drill PASSED"
