#!/usr/bin/env bash
#
# Offsite sync entry point (#827). Default transport is the incremental restic design; the pre-#827
# whole-repo tarball transport survives one release behind DOKTOK_OFFSITE_TRANSPORT=tarball.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "${DOKTOK_OFFSITE_TRANSPORT:-restic}" = "tarball" ]; then
    exec ./deploy/azure-sync-tarball.sh "$@"
fi

# Incremental restic transport (#827). Two Azure restic repos (created by azure-provision.sh):
#   azure:<container>:/files  <- restic backup of the files tree (same source => same chunk dedup)
#   azure:<container>:/pg     <- restic backup of the pgBackRest repo dir (write-once => perfect
#                                dedup; restore stays two-stage via azure-fetch.sh)
# GFS retention is restic forget metadata over shared chunks - no duplicate bytes (#766 tarballs
# kept up to 23 full copies per leg). Hourly cadence => 1h offsite RPO (pg WAL rides inside the
# pg repo; local RPOs are unchanged: WAL 60s, files 15min).
source deploy/lib.sh

: "${DOKTOK_AZURE_CONTAINER:?set DOKTOK_AZURE_CONTAINER}"
: "${DOKTOK_RESTIC_PASSWORD:?set DOKTOK_RESTIC_PASSWORD}"
min_sets="${DOKTOK_OFFSITE_MIN_SETS:-3}"
dry_run="${1:-}"

# fail_sync + the ERR trap come FIRST so every later failure (missing tools, missing repos, failed
# legs) writes the failure sentinel - the DRP contract holds on every exit path.
fail_sync() {
    write_status offsite false "azure sync failed: $1"
    err "azure sync FAILED: $1"
    exit 1
}
trap 'fail_sync "unexpected error"' ERR

# Fail fast, with the failure sentinel, on missing credentials - not mid-run after the uploads.
[ -n "${DOKTOK_AZURE_SAS:-}" ] || fail_sync "DOKTOK_AZURE_SAS is not set (sync credential, no-delete)"
[ -n "${DOKTOK_AZURE_SAS_PRUNE:-}" ] || fail_sync "DOKTOK_AZURE_SAS_PRUNE is not set (prune credential, host-only)"

mode="${DOKTOK_DEPLOY_MODE:-host}"
COMPOSE_FILES="${DOKTOK_COMPOSE_FILES:-docker-compose.prod.yml}"
COMPOSE_ENV_FILE="${DOKTOK_COMPOSE_ENV_FILE:-.env.production}"
compose=(docker compose)
for f in ${COMPOSE_FILES//,/ }; do compose+=(-f "$f"); done
compose+=(--env-file "$COMPOSE_ENV_FILE")

# Host mode needs a local restic; compose mode uses the backup-runner's (baked into the image).
# NOTE: do NOT write `require restic || true` - require() exits the whole script on failure.
if [ "$mode" != "compose" ]; then require restic; fi

[ -d "$FILES_REPO" ] || fail_sync "no files repo at $FILES_REPO - run a backup first"
[ -d "$BACKUP_DIR/pg" ] || fail_sync "no pg repo at $BACKUP_DIR/pg - run a backup first"

# restic sees the pg repo by its IN-RUNNER path in compose mode (/backups), host path otherwise.
if [ "$mode" = "compose" ]; then
    pg_src="/backups/pg"
else
    pg_src="$BACKUP_DIR/pg"
fi
export RESTIC_PASSWORD="$DOKTOK_RESTIC_PASSWORD"

# --- sync both legs (no-delete SAS; interruption-safe: content-addressed => rerun) -------------
# NOTE on locks: restic 0.14 creates+removes a lock even with --no-lock (spike 2026-08-18). With
# the no-delete sync SAS the removal fails (non-fatal, exit stays 0) and orphan locks pile up;
# the prune step below sweeps them (unlock --remove-all) under the delete-capable SAS.
# NOTE on copy: `restic copy --from-repo` would spare re-reading the files tree, but its
# cross-repo password auth failed consistently in the spike (restic 0.14) - direct backup keeps
# every leg on single-repo auth. Revisit as an optimization at scale.
offsite_azure_env sync
export RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/files"
echo "files: backup -> $RESTIC_REPOSITORY"
if [ "$dry_run" != "--dry-run" ]; then
    if [ "$mode" = "compose" ]; then
        # Reuse backup-files.sh's virtiofs staging (O_NOATIME workaround, #745): the runner's
        # service env already maps DOKTOK_FILES_STAGE_SRC=/host/files + DOKTOK_FILES_ROOT=/data/files.
        "${compose[@]}" run --rm -e AZURE_ACCOUNT_NAME -e AZURE_ACCOUNT_SAS \
            -e RESTIC_REPOSITORY -e RESTIC_PASSWORD backup-runner bash -c '
                set -e
                # stage only when the runner has the virtiofs workaround configured (#745);
                # prod mounts the live tree directly and must never wipe/copy it
                if [ -n "${DOKTOK_FILES_STAGE_SRC:-}" ]; then
                    find "$DOKTOK_FILES_ROOT" -mindepth 1 -delete
                    cp -a "$DOKTOK_FILES_STAGE_SRC/." "$DOKTOK_FILES_ROOT/"
                fi
                restic backup "$DOKTOK_FILES_ROOT" --tag files_root --host doktok --no-lock \
                    --exclude ".DS_Store" --exclude "Thumbs.db" --exclude "desktop.ini" \
                    --exclude ".localized"
            ' || fail_sync "files backup to Azure failed"
    else
        files_src="$FILES_ROOT"
        offsite_restic backup "$files_src" --tag files_root --host doktok --no-lock \
            --exclude ".DS_Store" --exclude "Thumbs.db" --exclude "desktop.ini" --exclude ".localized" \
            || fail_sync "files backup to Azure failed"
    fi
fi

export RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/pg"
echo "pg: backup $pg_src -> $RESTIC_REPOSITORY"
[ "$dry_run" = "--dry-run" ] || \
    offsite_restic backup "$pg_src" --no-lock --exclude log --exclude lock \
        || fail_sync "pg repo backup to Azure failed"

# --- GFS retention: snapshot metadata, not duplicate bytes (the only delete-capable step) -------
if [ "$dry_run" != "--dry-run" ]; then
    offsite_azure_env prune
    for prefix in files pg; do
        export RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/${prefix}"
        # Sweep the sync steps' orphan locks first (sync SAS can't delete); safe because sync and
        # prune are sequential within ONE run.
        offsite_restic unlock --remove-all 2>/dev/null || true
        offsite_restic forget --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --keep-yearly 1 \
            || warn "prune failed for $prefix (retry next run)"
    done
    offsite_azure_env sync
fi

# --- audit + sentinel (contract unchanged) -------------------------------------------------------
files_sets="$(RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/files" offsite_snapshot_count)"
pg_sets="$(RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/pg" offsite_snapshot_count)"
detail="offsite snapshots pg=${pg_sets} files=${files_sets} (hourly restic; content current)"
if [ "$dry_run" = "--dry-run" ]; then
    warn "dry-run - would backup the files tree + $pg_src, then prune 7d/4w/12m/1y"
elif [ "${pg_sets:-0}" -lt "$min_sets" ] || [ "${files_sets:-0}" -lt "$min_sets" ]; then
    write_status offsite true "WARN: fewer than ${min_sets} offsite snapshots - ${detail}"
    warn "offsite history below the minimum (${min_sets}); it builds up over the next runs"
else
    write_status offsite true "$detail"
fi
[ "$dry_run" = "--dry-run" ] || log_event offsite success true "$detail" '"item_count":'"${pg_sets}"
ok "offsite sync complete ($detail)"
