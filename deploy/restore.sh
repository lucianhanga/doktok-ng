#!/usr/bin/env bash
#
# Orchestrate a full restore from the local repository (M12 #363): Postgres (pgBackRest, optional
# PITR) + files_root (restic). DESTRUCTIVE. Restore the DB to a point AT OR BEFORE the files restore
# point (files must be >= the DB restore point), then run `doktok-worker repair` to reconcile.
#
# Usage:  ./deploy/restore.sh <files-target-dir> ["YYYY-MM-DD HH:MM:SS+00"]
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

files_target="${1:?usage: restore.sh <files-target-dir> [pitr-time]}"
pitr="${2:-}"

warn "this restores Postgres (stop it first) and files_root. Ctrl-C within 5s to abort."
sleep 5
./deploy/restore-pg.sh "$pitr"
./deploy/restore-files.sh "$files_target"
ok "restore complete - start the stack, then run: doktok-worker repair"
