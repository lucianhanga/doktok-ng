#!/usr/bin/env bash
#
# Restore the files_root tree from the local restic repository (M12). DESTRUCTIVE if the target is a
# live files_root - restore into staging or an empty dir, then swap.
#
# Usage:  ./deploy/restore-files.sh <target-dir> [snapshot-id]   (snapshot defaults to latest)
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

require restic
: "${DOKTOK_RESTIC_PASSWORD:?set DOKTOK_RESTIC_PASSWORD}"
export RESTIC_REPOSITORY="$FILES_REPO"
export RESTIC_PASSWORD="$DOKTOK_RESTIC_PASSWORD"
trap 'err "files restore FAILED"; exit 1' ERR

target="${1:?usage: restore-files.sh <target-dir> [snapshot-id]}"
snapshot="${2:-latest}"
mkdir -p "$target"

echo "restoring snapshot $snapshot -> $target"
restic restore "$snapshot" --target "$target"
ok "files_root restored from snapshot $snapshot to $target"
