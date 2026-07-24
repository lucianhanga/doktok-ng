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

# restic recreates the snapshot's absolute path under --target (a snapshot of /x/y restores into
# <target>/x/y), so restore into a scratch dir first and then move the tree into place.
scratch="$(mktemp -d "${target}.scratch.XXXXXX")"
abs_root="$(cd "$FILES_ROOT" && pwd -P)"
echo "restoring snapshot $snapshot -> $target"
restic restore "$snapshot" --target "$scratch"
find "$target" -mindepth 1 -delete
cp -a "$scratch$abs_root/." "$target/"
rm -rf "$scratch"

# Staging copy-back (dev-on-macOS workaround, #745; see backup-files.sh): when the files_root was
# staged through a container-local dir, a restore into the live files_root must be synced back to
# the real tree at DOKTOK_FILES_STAGE_SRC. Restores into other targets (test dirs) stay untouched.
if [ -n "${DOKTOK_FILES_STAGE_SRC:-}" ] && [ "$target" = "$FILES_ROOT" ]; then
    echo "syncing restored tree -> $DOKTOK_FILES_STAGE_SRC (virtiofs O_NOATIME workaround)"
    find "$DOKTOK_FILES_STAGE_SRC" -mindepth 1 -delete
    cp -a "$target/." "$DOKTOK_FILES_STAGE_SRC/"
fi
ok "files_root restored from snapshot $snapshot to $target"
