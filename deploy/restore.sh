#!/usr/bin/env bash
#
# Orchestrate a full restore from the local repository (M12 #363): Postgres (pgBackRest, optional
# PITR) + files_root (restic). DESTRUCTIVE. Restore the DB to a point AT OR BEFORE the files restore
# point (files must be >= the DB restore point), then run `doktok-worker repair` to reconcile.
#
# Usage:  ./deploy/restore.sh <files-target-dir> ["YYYY-MM-DD HH:MM:SS+00"]
#
# Modes: host (tools on the machine) and compose (DOKTOK_DEPLOY_MODE=compose) - the SAME script on
# the Mac dev box and on the prod host (#745).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

files_target="${1:?usage: restore.sh <files-target-dir> [pitr-time]}"
pitr="${2:-}"

mode="${DOKTOK_DEPLOY_MODE:-host}"
COMPOSE_FILES="${DOKTOK_COMPOSE_FILES:-docker-compose.prod.yml}"
COMPOSE_ENV_FILE="${DOKTOK_COMPOSE_ENV_FILE:-.env.production}"
compose=(docker compose)
for f in ${COMPOSE_FILES//,/ }; do
    compose+=(-f "$f")
done
compose+=(--env-file "$COMPOSE_ENV_FILE")

warn "this restores Postgres (stop it first) and files_root. Ctrl-C within 5s to abort."
sleep 5

if [ "$mode" = "compose" ]; then
    # Map the host target into the runner's mounts: the live files_root -> /data/files; a staging
    # path under the backup dir -> /backups/...
    case "$files_target" in
        "$FILES_ROOT"|"$FILES_ROOT"/*|./storage/files*|storage/files*)
            runner_target="/data/files" ;;
        "$BACKUP_DIR"*|./backups*|backups*)
            runner_target="/backups/${files_target#"${BACKUP_DIR%/}"}" ;;
        *)
            err "in compose mode restore into the files_root ($FILES_ROOT) or under $BACKUP_DIR"
            exit 1
            ;;
    esac
    # Stop the db container, then restore the base backup (optional PITR) from a one-off container
    # sharing the data + repo volumes; files_root is restored by the backup-runner (restic there).
    # The log/lock dirs are NOT created by stanza-create; without them pgbackrest warns + logs nowhere.
    "${compose[@]}" exec -u postgres -T db mkdir -p /var/lib/doktok/pg/log /var/lib/doktok/pg/lock 2>/dev/null || true
    "${compose[@]}" stop db || true
    pitr_args=()
    if [ -n "$pitr" ]; then
        pitr_args=(--type=time "--target=$pitr" --target-action=promote)
    fi
    "${compose[@]}" run --rm -u postgres --entrypoint pgbackrest db --stanza=doktok restore --delta \
        ${pitr_args[@]+"${pitr_args[@]}"}
    "${compose[@]}" run --rm backup-runner deploy/restore-files.sh "$runner_target"
    "${compose[@]}" up -d db
    ok "restore complete - start the stack, then run: doktok-worker repair"
    exit 0
fi

./deploy/restore-pg.sh "$pitr"
./deploy/restore-files.sh "$files_target"
ok "restore complete - start the stack, then run: doktok-worker repair"
