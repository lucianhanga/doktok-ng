#!/usr/bin/env bash
#
# Orchestrate a full local-first backup (M12 #363): files_root (restic) + Postgres (pgBackRest) into
# $DOKTOK_BACKUP_DIR. Called by deploy.yml before a deploy and by the systemd timers. Offsite sync to
# Azure is a separate step (azure-sync.sh); the weekly logical safety-net is backup-pg-logical.sh.
#
# Supersedes the old pg_dump+tar snapshot (DEVOPS-6): the engine gives low-RPO PITR + dedup snapshots
# and writes the freshness sentinels the DRP panel reads. Arg 1: pg backup type (full|diff|incr).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

warn "backups are secret-bearing (encrypted) - store the repo + keys off the box"

type="${1:-incr}"
mode="${DOKTOK_DEPLOY_MODE:-host}"

# Compose files/env for the containerized path: prod defaults; the dev override passes
# "docker-compose.yml,docker-compose.dev.yml" + .env (issue #745), so the SAME scripts run
# identically on the Mac dev box and on the prod host.
COMPOSE_FILES="${DOKTOK_COMPOSE_FILES:-docker-compose.prod.yml}"
COMPOSE_ENV_FILE="${DOKTOK_COMPOSE_ENV_FILE:-.env.production}"
compose=(docker compose)
for f in ${COMPOSE_FILES//,/ }; do
    compose+=(-f "$f")
done
compose+=(--env-file "$COMPOSE_ENV_FILE")

# Anomaly guard (#747): count live documents and refuse to back up a database that looks destroyed,
# so a post-disaster run can't overwrite/expire the last good backup history. A guard that can't
# count (db down, no psql) never blocks the backup - it only warns.
cur_docs=""
if [ "$mode" = "compose" ]; then
    cur_docs="$("${compose[@]}" exec -T db psql -U doktok -d doktok -tAc \
        'select count(*) from documents' 2>/dev/null | tr -d '[:space:]' || true)"
elif command -v psql >/dev/null 2>&1; then
    cur_docs="$(psql "$DATABASE_URL" -tAc 'select count(*) from documents' 2>/dev/null \
        | tr -d '[:space:]' || true)"
fi
case "$cur_docs" in
    ''|*[!0-9]*) warn "anomaly guard: could not count documents - continuing without the guard" ;;
    *) backup_anomaly_guard "$cur_docs" || exit 1 ;;
esac

if [ "$mode" = "compose" ]; then
    # Containerized (staging/prod/dev): same scripts, run where the tools + data are (M12 #377). Files
    # run in the backup-runner (restic + mounts); pg runs inside the db container (pgbackrest lives
    # there), then the runner records the pg sentinel into the shared backup dir.
    "${compose[@]}" run --rm backup-runner deploy/backup-files.sh
    # Bootstrap the stanza if the repo is fresh/was wiped (host mode does the same in backup-pg.sh);
    # without it pgbackrest backup crashes with a raw 139 instead of a clear error, and WAL
    # archiving stays broken after a repo wipe. The log/lock dirs are NOT created by stanza-create;
    # without them every pgbackrest run warns "unable to open log file" and logs nowhere.
    "${compose[@]}" exec -u postgres -T db mkdir -p /var/lib/doktok/pg/log /var/lib/doktok/pg/lock
    "${compose[@]}" exec -u postgres -T db pgbackrest --stanza=doktok stanza-create 2>/dev/null || true
    # Run pgbackrest as the postgres user (the WAL archive_command runs as that uid), so the repo
    # files - notably archive.info - stay owned by postgres and remain readable by archive-push.
    # `exec` defaults to root, which would write root-owned files and break WAL archiving (#377).
    "${compose[@]}" exec -u postgres -T db pgbackrest --stanza=doktok backup --type="$type"
    # Capture pg metrics (repo size, db size, backup label) for the DRP (M12 #380). pgbackrest emits
    # JSON; parse it on the host (the db image has no python). Best-effort: empty extra on any failure.
    pg_extra="$("${compose[@]}" exec -u postgres -T db pgbackrest --stanza=doktok info --output=json 2>/dev/null \
        | pg_backup_extra || true)"
    # Record the live document count as the anomaly guard's next baseline (#747).
    case "$cur_docs" in
        ''|*[!0-9]*) ;;
        *) pg_extra="${pg_extra:+${pg_extra},}\"doc_count\":${cur_docs}" ;;
    esac
    "${compose[@]}" run --rm backup-runner deploy/write-status.sh pg true "pgbackrest $type" "$pg_extra"
    # Record the pg leg into the append-only history too (M12 DRP hardening), from the runner that
    # has the shared backup dir mounted. The files leg logs its own history inside backup-files.sh.
    "${compose[@]}" run --rm backup-runner deploy/log-event.sh pg success true "pgbackrest $type" "$pg_extra"
else
    # Host (dev/test): tools installed on the host, host file paths.
    ./deploy/backup-files.sh
    ./deploy/backup-pg.sh "$type"
fi
ok "backup complete (mode=$mode) -> ${BACKUP_DIR}"
