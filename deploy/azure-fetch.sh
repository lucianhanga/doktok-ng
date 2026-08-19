#!/usr/bin/env bash
#
# Fetch the offsite backup repos from Azure into a local staging dir (#359, restic transport #827).
# Rebuilds BOTH repos under the staging dir (chunk-deduped - only missing chunks cross the wire):
#   staging/files  restic repo   (restore latest tree from azure:/files, then init+backup to rebuild)
#   staging/pg     pgBackRest repo (via restic restore of the azure:/pg snapshot)
# The SAME deploy/restore.sh then restores from staging (point DOKTOK_BACKUP_DIR at it).
#
# Usage: ./deploy/azure-fetch.sh [staging-dir] [timestamp]
#   timestamp  yyyymmdd[-hhmmss]; selects the newest /pg snapshot at or before it (default: latest)
#
# Env: as azure-sync.sh (DOKTOK_AZURE_ACCOUNT/CONTAINER/SAS, DOKTOK_RESTIC_PASSWORD).
#
# The best-effort repo verification steps at the end (restic snapshots, pgbackrest info) run
# through docker compose and need the db/backup-runner images available; without them the
# fetches still complete and the checks report "skipped/failed - check by hand".
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

: "${DOKTOK_AZURE_CONTAINER:?set DOKTOK_AZURE_CONTAINER}"
: "${DOKTOK_RESTIC_PASSWORD:?set DOKTOK_RESTIC_PASSWORD}"
trap 'err "azure fetch FAILED"; exit 1' ERR

staging="${1:-./backups.azure-restore}"
want_ts="${2:-}"
mode="${DOKTOK_DEPLOY_MODE:-host}"
COMPOSE_FILES="${DOKTOK_COMPOSE_FILES:-docker-compose.prod.yml}"
COMPOSE_ENV_FILE="${DOKTOK_COMPOSE_ENV_FILE:-.env.production}"
compose=(docker compose)
for f in ${COMPOSE_FILES//,/ }; do compose+=(-f "$f"); done
compose+=(--env-file "$COMPOSE_ENV_FILE")

offsite_azure_env sync
export RESTIC_PASSWORD="$DOKTOK_RESTIC_PASSWORD"
mkdir -p "$staging/files" "$staging/pg"
staging_abs="$(cd "$staging" && pwd)"
# Idempotent re-runs: drop leftover scratch trees from previous (possibly failed) fetches -
# restic restore refuses to recreate existing entries (e.g. the pg repo's `latest` symlink).
rm -rf "$staging_abs/.filestree" "$staging_abs/.pgtree"
require python3  # the snapshot picker runs host-side in both modes (lib.sh relies on host python3 too)
if [ "$mode" != "compose" ]; then require restic; fi

# staging_restic <args...> - restic with the staging dir in scope: host mode runs the local
# restic; compose mode runs the runner with $staging_abs bind-mounted at /backups (callers then
# use /backups/... paths). Forwards the Azure env too, so the same helper serves the Azure
# restores AND the local repo rebuild.
staging_restic() {
    if [ "$mode" = "compose" ]; then
        "${compose[@]}" run --rm -v "$staging_abs:/backups" \
            -e RESTIC_REPOSITORY -e RESTIC_PASSWORD -e AZURE_ACCOUNT_NAME -e AZURE_ACCOUNT_SAS \
            backup-runner restic "$@"
    else
        restic "$@"
    fi
}

# Mode-specific path spaces: the pg restore target must be runner-visible in compose mode (the
# files leg handles its own container-local paths inline, see below). Host-only rebuild paths are
# plain host paths.
if [ "$mode" = "compose" ]; then
    pgtree_target="/backups/.pgtree"
else
    pgtree_target="$staging_abs/.pgtree"
    tree_target="$staging_abs/.filestree"; st_repo="$staging_abs/files"
fi

# pick_pg_snapshot - prints the newest /pg snapshot id at/before $want_ts (empty if none).
# Called as LATE as possible (right before the pg leg): the files leg takes minutes, and the
# hourly sync's prune collapses same-day snapshots (keep-daily), so an early pick can be deleted
# underfoot. The pg restore retries once with a fresh pick on failure.
pick_pg_snapshot() {
    RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/pg" offsite_restic snapshots --no-lock --json | python3 -c '
import sys, json
want = sys.argv[1].replace("-", "")
if len(want) == 8:  # date-only means "up to end of that day"
    want += "235959"
snaps = sorted(json.load(sys.stdin), key=lambda s: s["time"])
pick = ""
for s in snaps:
    stamp = s["time"][:19].replace("-", "").replace("T", "").replace(":", "")
    if not want or stamp <= want:
        pick = s["id"]
print(pick)
' "$want_ts"
}

# files leg: restore the latest tree from Azure, then rebuild a local restic repo from it (the
# restore engine consumes staging/files as a repo; single-repo auth only - restic copy's
# --from-repo auth proved unreliable on restic 0.14, spike 2026-08-18). In compose mode the
# restore+rebuild run ENTIRELY inside the runner on container-local storage: restic reads sources
# with O_NOATIME, which virtiofs bind mounts fail with EIO (#745, same class as the sync staging).
# The container name goes in as $1 (no quote splicing); the rebuilt snapshot's recorded path is
# /tmp/filestree<orig> - restore-files.sh resolves paths from snapshot metadata, so that is fine.
if [ "$mode" = "compose" ]; then
    "${compose[@]}" run --rm -v "$staging_abs:/backups" \
        -e AZURE_ACCOUNT_NAME -e AZURE_ACCOUNT_SAS -e RESTIC_PASSWORD \
        backup-runner bash -c '
            set -e
            rm -rf /tmp/filestree
            RESTIC_REPOSITORY="azure:$1:/files" restic restore latest --no-lock --target /tmp/filestree
            src="$(RESTIC_REPOSITORY="azure:$1:/files" restic snapshots latest --no-lock --json \
                | sed -n "s/.*\"paths\":\[\"\([^\"]*\)\"\].*/\1/p")"
            [ -n "$src" ] && [ -d "/tmp/filestree$src" ] || { echo "restored files tree missing" >&2; exit 1; }
            restic -r /backups/files init 2>/dev/null || true
            restic -r /backups/files backup "/tmp/filestree$src" --tag files_root --host doktok >/dev/null
            rm -rf /tmp/filestree
        ' _ "$DOKTOK_AZURE_CONTAINER"
else
    files_path="$(RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/files" offsite_restic snapshots latest --no-lock --json \
        | sed -n 's/.*"paths":\["\([^"]*\)"\].*/\1/p')"
    [ -n "$files_path" ] || { err "no /files offsite snapshot"; exit 1; }
    RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/files" \
        staging_restic restore latest --no-lock --target "$tree_target"
    tree_root="$staging_abs/.filestree$files_path"
    [ -d "$tree_root" ] || { err "restored files tree missing at $tree_root"; exit 1; }
    RESTIC_REPOSITORY="$st_repo" staging_restic init 2>/dev/null || true
    RESTIC_REPOSITORY="$st_repo" staging_restic backup "$tree_root" --tag files_root --host doktok >/dev/null
    rm -rf "$staging_abs/.filestree"
fi
# pg leg: pick the snapshot NOW (late - see pick_pg_snapshot) and restore it; restic recreates
# the original absolute paths under the target. The repo root can land at any depth/name (the
# sync stages it at /tmp/pg-repo in compose mode), so locate it structurally: the dir that
# CONTAINS the pgBackRest `archive` dir IS the repo root. -print -quit: first match, no SIGPIPE.
snap_id="$(pick_pg_snapshot)"
[ -n "$snap_id" ] || { err "no /pg offsite snapshot at/before '${want_ts:-latest}'"; exit 1; }
echo "pg: restore snapshot ${snap_id} -> ${staging}/pg"
if ! RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/pg" \
        staging_restic restore "$snap_id" --no-lock --target "$pgtree_target"; then
    # The hourly sync's prune may have deleted the pick underfoot (same-day collapse); retry once
    # with a fresh pick.
    warn "pg restore failed - re-picking the latest snapshot and retrying once"
    snap_id="$(pick_pg_snapshot)"
    [ -n "$snap_id" ] || { err "no /pg offsite snapshot at/before '${want_ts:-latest}'"; exit 1; }
    RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/pg" \
        staging_restic restore "$snap_id" --no-lock --target "$pgtree_target"
fi
archive_dir="$(find "$staging_abs/.pgtree" -type d -name archive -print -quit)"
[ -n "$archive_dir" ] || { err "restored pg snapshot has no pg archive dir"; exit 1; }
cp -a "$(dirname "$archive_dir")/" "$staging_abs/pg/"
rm -rf "$staging_abs/.pgtree"

# Verify both repos are readable (same best-effort checks as the tarball design; the compose
# array was already built above).
if "${compose[@]}" run --rm -v "$staging_abs:/backups" backup-runner \
        bash -c 'export RESTIC_REPOSITORY=/backups/files RESTIC_PASSWORD="$DOKTOK_RESTIC_PASSWORD"; restic snapshots >/dev/null' 2>/dev/null; then
    ok "restic repo readable"
else
    warn "restic verify skipped/failed - check by hand before restoring"
fi
if "${compose[@]}" run --rm -u postgres -v "$staging_abs/pg:/var/lib/doktok/pg" \
        --entrypoint pgbackrest db --stanza=doktok info >/dev/null 2>&1; then
    ok "pgBackRest repo readable"
else
    warn "pgBackRest verify skipped/failed - check by hand before restoring"
fi

ok "offsite state staged at ${staging}"
echo
echo "restore from it with:"
echo "  DOKTOK_BACKUP_DIR=${staging} ./deploy/restore.sh ./storage/files   # (+ optional PITR arg)"
echo "  (dev:  DOKTOK_BACKUP_DIR=${staging} make dev-restore FILES_TARGET=./storage/files)"
