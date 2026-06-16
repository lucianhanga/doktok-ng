#!/usr/bin/env bash
#
# Snapshot the files_root tree into the local restic repository (M12 #345/DEVOPS-A2 local half).
# restic gives content-dedup + AES-256 encryption, so frequent snapshots are cheap and the staged
# repo is ciphertext-at-rest. azure-sync.sh later ships this repo offsite.
#
# Env: DOKTOK_RESTIC_PASSWORD (required), DOKTOK_BACKUP_DIR (default ./backups),
#      DOKTOK_FILES_ROOT (default ./storage/files).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

require restic
: "${DOKTOK_RESTIC_PASSWORD:?set DOKTOK_RESTIC_PASSWORD (and store it OFF the box)}"
export RESTIC_REPOSITORY="$FILES_REPO"
export RESTIC_PASSWORD="$DOKTOK_RESTIC_PASSWORD"
trap 'err "files backup FAILED"; exit 1' ERR

mkdir -p "$FILES_REPO"
if ! restic cat config >/dev/null 2>&1; then
    warn "initialising restic repo at $FILES_REPO"
    restic init >/dev/null
fi

echo "snapshotting $FILES_ROOT -> $FILES_REPO"
restic backup "$FILES_ROOT" --tag files_root --host doktok
# Keep a sensible history; prune unreferenced data so the local repo stays small.
restic forget --tag files_root --keep-daily 14 --keep-weekly 8 --keep-monthly 6 --prune >/dev/null

ok "files_root snapshot complete"
restic snapshots --latest 1
