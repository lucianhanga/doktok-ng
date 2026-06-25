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
trap 'write_status files false "backup failed"; err "files backup FAILED"; exit 1' ERR

mkdir -p "$FILES_REPO"
if ! restic cat config >/dev/null 2>&1; then
    warn "initialising restic repo at $FILES_REPO"
    restic init >/dev/null
fi

echo "snapshotting $FILES_ROOT -> $FILES_REPO"
out="$(restic backup "$FILES_ROOT" --tag files_root --host doktok 2>&1)"
printf '%s\n' "$out"
# Keep a sensible history; prune unreferenced data so the local repo stays small.
restic forget --tag files_root --keep-daily 14 --keep-weekly 8 --keep-monthly 6 --prune >/dev/null

# Parse restic's summary for the DRP metrics (M12 #380; no jq dependency).
fcount="$(printf '%s' "$out" | grep -oE 'processed [0-9]+ files' | grep -oE '[0-9]+' | head -1)"
fsize="$(printf '%s' "$out" | grep -oE 'processed [0-9]+ files, [0-9.]+ [KMGTP]?i?B' | sed -E 's/.*files, //' | head -1)"
snap="$(printf '%s' "$out" | grep -oE 'snapshot [0-9a-f]+ saved' | grep -oE '[0-9a-f]+' | head -1)"
write_status files true "restic snapshot" \
    "\"size\":\"${fsize}\",\"file_count\":${fcount:-0},\"backup_id\":\"${snap}\""
ok "files_root snapshot complete (${fcount:-?} files, ${fsize:-?})"
restic snapshots --latest 1
