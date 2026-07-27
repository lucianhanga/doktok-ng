#!/usr/bin/env bash
#
# make restore-drill-dev - DESTRUCTIVE dev disaster drill on the REAL engine (#755).
#
# The full loop with the production backup/restore path (pgBackRest + restic via backup.sh /
# restore.sh), and 4-layer verification that EVERYTHING came back:
#   1. BASELINE  - per-table row counts + file count (lib-drill-verify.sh),
#   2. BACK UP   - backup.sh (aborts before wiping if it fails, so you are never left with nothing),
#   3. WIPE      - DROP SCHEMA + empty files_root,
#   4. RESTORE   - restore.sh with PITR to just before the wipe,
#   5. VERIFY    - row counts vs baseline (exact), sha256 spot-check of restored originals,
#                  API smoke (documents total), drill sentinel + history (the DRP drill leg).
#
# It only ever targets the LOCAL dev container (`doktok-db`) and asks for confirmation (skip with
# FORCE=1). Stop `make run-backend` / `make run-worker` first (their connections drop on the wipe).
# Note: a scheduled (cron) backup firing mid-drill is blocked by the anomaly guard (#747) - the
# wiped DB looks exactly like the collapse the guard refuses to back up.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh
source deploy/lib-drill-verify.sh

G=$'\e[0;32m'; R=$'\e[0;31m'; Y=$'\e[0;33m'; N=$'\e[0m'
ok() { echo "${G}$*${N}"; }
warn() { echo "${Y}$*${N}"; }
err() { echo "${R}$*${N}" >&2; }

DB_CONTAINER="${DOKTOK_DB_CONTAINER:-doktok-db}"
FILES_ROOT="${DOKTOK_FILES_ROOT:-storage/files}"
BACKUP_TYPE="${DOKTOK_DRILL_BACKUP_TYPE:-full}"
API_URL="${DOKTOK_API_URL:-http://127.0.0.1:8000}"
COMPOSE_ENV_FILE="${DOKTOK_COMPOSE_ENV_FILE:-.env}"

drill_t0="$(date +%s)"

fail_drill() {
    local msg="$1"
    write_status drill false "dev drill failed: ${msg}"
    log_event drill drill_fail false "dev drill failed: ${msg}"
    err "DRILL FAILED - ${msg}"
    exit 1
}
trap 'fail_drill "unexpected error"' ERR

command -v docker >/dev/null 2>&1 || { err "docker not found on PATH"; exit 1; }
docker inspect "$DB_CONTAINER" >/dev/null 2>&1 || {
    err "live db container '$DB_CONTAINER' is not running - start it with: make db"
    exit 1
}
# Safety rail: never let an unset/odd files root turn a wipe into 'rm -rf /'.
case "$FILES_ROOT" in
    ""|"/"|"/*") err "refusing to run: unsafe FILES_ROOT='$FILES_ROOT'"; exit 1 ;;
esac
[ -d "$FILES_ROOT" ] || { err "FILES_ROOT '$FILES_ROOT' is not a directory"; exit 1; }

if [ "${FORCE:-0}" != "1" ]; then
    echo "${Y}This DELETES all data in the dev database ('$DB_CONTAINER') and $FILES_ROOT,"
    echo "then restores it via the REAL engine (backup.sh -> restore.sh PITR)."
    echo "Stop run-backend/run-worker before continuing.${N}"
    read -r -p "Type 'wipe' to proceed: " ans
    [ "$ans" = "wipe" ] || { warn "aborted - nothing changed"; exit 1; }
fi

# 1. BASELINE
echo "=== 1/5 baseline ==="
baseline_counts="$(drill_db_counts compose)"
[ -n "$baseline_counts" ] || fail_drill "could not read the live DB counts"
base_files="$(find "$FILES_ROOT" -type f ! -name '.DS_Store' 2>/dev/null | wc -l | tr -d ' ')"
base_docs="$(printf '%s' "$baseline_counts" | cut -d'|' -f1)"
ok "baseline: counts=[${baseline_counts}] files=${base_files}"

# 2. BACK UP (abort before the wipe if this fails)
echo "=== 2/5 backup (${BACKUP_TYPE}) via the real engine ==="
./deploy/backup.sh "$BACKUP_TYPE" >/dev/null || fail_drill "backup failed - NOT wiping anything"
ok "fresh recovery point secured"

# 3. WIPE
echo "=== 3/5 WIPE: DROP SCHEMA + empty files_root ==="
pitr_target="$(date -u +'%Y-%m-%d %H:%M:%S+00')"
sleep 2  # keep the PITR target strictly before the destructive statements
find "$FILES_ROOT" -mindepth 1 -delete 2>/dev/null || true
docker exec -e PGPASSWORD=doktok "$DB_CONTAINER" psql -U doktok -d doktok -v ON_ERROR_STOP=1 \
    -c "DROP SCHEMA public CASCADE;" -c "CREATE SCHEMA public;" >/dev/null
ok "wiped (PITR target: ${pitr_target})"

# 4. RESTORE via the real engine (pgBackRest PITR + restic latest)
echo "=== 4/5 restore (PITR ${pitr_target}) ==="
./deploy/restore.sh "$FILES_ROOT" "$pitr_target" >/dev/null || \
    fail_drill "restore.sh failed - recover by hand from ./backups"
# Wait for the db to accept connections again (restore.sh restarts it).
ready=0
for _ in $(seq 1 30); do
    if docker exec "$DB_CONTAINER" pg_isready -U doktok -d doktok >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 2
done
[ "$ready" = "1" ] || fail_drill "db never became ready after the restore"

# 5. VERIFY (4 layers)
echo "=== 5/5 verify ==="
fail=0
restored_counts="$(drill_db_counts compose)"
if drill_counts_match "$baseline_counts" "$restored_counts"; then
    ok "row counts match baseline: [${restored_counts}]"
else
    err "row count mismatch (see above)"
    fail=1
fi

after_files="$(find "$FILES_ROOT" -type f ! -name '.DS_Store' 2>/dev/null | wc -l | tr -d ' ')"
if [ "$base_files" = "$after_files" ]; then
    ok "files_root: ${base_files} -> ${after_files} restored"
else
    err "files_root MISMATCH: ${base_files} -> ${after_files}"
    fail=1
fi

sha_provider="docker exec ${DB_CONTAINER} psql -U doktok -d doktok -tAc \"SELECT id || '|' || sha256 FROM documents\""
if drill_verify_hashes "$FILES_ROOT" "$sha_provider" 25; then
    ok "original-byte hash sample ok"
else
    err "original-byte hash check failed"
    fail=1
fi

# API smoke: the documents list must report the baseline total. Static dev token from .env.
token="$(grep '^DOKTOK_TENANT_TOKENS=' "$COMPOSE_ENV_FILE" 2>/dev/null | cut -d= -f2- \
    | sed -n 's/.*"\([^"]*\)"[[:space:]]*:.*/\1/p' | head -1)"
if [ -n "$token" ]; then
    total="$(curl -sf -H "Authorization: Bearer ${token}" \
        "${API_URL}/api/v1/documents?page_size=1" 2>/dev/null \
        | sed -n 's/.*"total":\([0-9]*\).*/\1/p' | head -1 || true)"
    if [ "$total" = "$base_docs" ]; then
        ok "API smoke: /documents total=${total}"
    elif [ -z "$total" ]; then
        warn "API smoke skipped (${API_URL} unreachable - start the backend and check by hand)"
    else
        err "API smoke MISMATCH: /documents total=${total} != baseline ${base_docs}"
        fail=1
    fi
else
    warn "API smoke skipped (no static token in ${COMPOSE_ENV_FILE})"
fi

rto_seconds="$(( $(date +%s) - drill_t0 ))"
evidence="rows=[${restored_counts}] files=${after_files}/${base_files} hashes=ok api=${total:-skip} rto=${rto_seconds}s"
echo
if [ "$fail" = 0 ]; then
    write_status drill true "dev drill: ${evidence}"
    log_event drill drill_pass true "dev drill: ${evidence}" "\"item_count\":${after_files}"
    ok "DRILL PASSED (${evidence})"
    echo "Restart run-backend/run-worker to reconnect."
else
    write_status drill false "dev drill failed: ${evidence}"
    log_event drill drill_fail false "dev drill failed: ${evidence}"
    err "DRILL FAILED (${evidence}) - see mismatches above; the last good backup is in ./backups"
    exit 1
fi
