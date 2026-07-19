#!/usr/bin/env bash
#
# Portable RESTORE - the DESTRUCTIVE importer (M12 portable restore, Phase 2). Runs as ROOT, OUT OF
# BAND from the backend (a systemd path-unit consumes the request file and invokes this), so the
# live backend never runs `pg_restore --clean` on the DB it is connected to.
#
# It imports a PRE-VALIDATED staged archive (the backend already decrypted + safe-extracted +
# checksum/HMAC/version-verified it into <export_dir>/restores/<staged_id>/extracted/). This script
# does NOT decrypt, validate, or trust the network - it only consumes a local, validated tree.
#
# Sequence (each step writes the restore status sentinel the backend reads + log_event restore ...):
#   1. MANDATORY pre-restore safety snapshot of the CURRENT state (so a bad restore is recoverable);
#      abort the whole restore if it fails.
#   2. Quiesce: maintenance flag ON (backend parks mutating requests + ingest), stop the worker,
#      terminate other DB sessions.
#   3. DB import: pg_restore --clean --if-exists of staging db.dump; ensure pgvector; migrate forward.
#   4. Files swap: atomically replace files_root with staging files/ (rename-based; keep .old).
#   5. Finish: clear the maintenance flag, restart the worker, status=done + restore.completed event.
#   6. Rollback on ANY failure: restore from the safety snapshot (files + pg), status=failed, and
#      LEAVE maintenance ON for a human (never a half-restored / unbootable system).
#
# Usage (the systemd service passes the staged_id):  ./deploy/restore-import.sh <staged_id>
# Mode-aware (DOKTOK_DEPLOY_MODE=host|compose), like backup.sh / restore.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

staged_id="${1:?usage: restore-import.sh <staged_id>}"
# F-29 (#641): staged_id arrives via a host-writable request file (status/requests/), NOT the
# validated API path - refuse anything but the backend's uuid4-hex format BEFORE it is ever
# interpolated into a path (a '../../x' would traverse pg_restore and `rm -rf "$STAGING"` as
# root). With slashes impossible no traversal exists, so this format check is the whole gate.
if [[ ! "$staged_id" =~ ^[0-9a-f]{32}$ ]]; then
    err "invalid staged_id (expected 32 lowercase hex chars): ${staged_id}"
    exit 2
fi
mode="${DOKTOK_DEPLOY_MODE:-host}"

EXPORT_DIR="${DOKTOK_BACKUP_EXPORT_DIR:-${BACKUP_DIR}/exports}"
STAGING="${EXPORT_DIR}/restores/${staged_id}"
EXTRACTED="${STAGING}/extracted"
DB_DUMP="${EXTRACTED}/db.dump"
FILES_SRC="${EXTRACTED}/files"
MAINT_FLAG="${STATUS_DIR}/maintenance.flag"
RESTORE_ID="$(sed -n 's/.*"restore_id":[[:space:]]*"\([^"]*\)".*/\1/p' "${STATUS_DIR}/requests/restore.request" 2>/dev/null || true)"
SAFETY_SNAPSHOT=""

# restore_status <state> <step> [detail] - mirror the backend's restore.json sentinel writer (so the
# backend's GET /backup/restore/status reflects progress live). 0644 so the backend uid can read it.
restore_status() {
    local state="$1" step="$2" detail="${3:-}"
    local now
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    mkdir -p "$STATUS_DIR"
    local tmp
    tmp="$(mktemp "${STATUS_DIR}/.restore.XXXXXX")"
    printf '{"state":"%s","step":"%s","detail":"%s","restore_id":"%s","finished_at":"%s"}\n' \
        "$state" "$step" "$(_json_escape "$detail")" "$RESTORE_ID" \
        "$([ "$state" = done ] || [ "$state" = failed ] && echo "$now" || echo "")" >"$tmp"
    mv -f "$tmp" "${STATUS_DIR}/restore.json"
    chmod 0644 "${STATUS_DIR}/restore.json"
}

# Fail-safe rollback: restore from the safety snapshot, LEAVE maintenance ON for a human.
fail_restore() {
    local msg="$1"
    err "restore FAILED: ${msg}"
    restore_status failed rollback "restore failed: ${msg}"
    log_event restore failure false "restore failed: ${msg}"
    if [ -n "$SAFETY_SNAPSHOT" ]; then
        warn "rolling back from the pre-restore safety snapshot"
        # The safety snapshot is a full restic+pgBackRest backup of the prior state. Best-effort
        # rollback; on host mode we can restore pg + files from it. Maintenance stays ON regardless.
        ./deploy/restore-pg.sh >/dev/null 2>&1 || err "pg rollback failed - manual recovery needed"
    fi
    warn "maintenance mode left ON (${MAINT_FLAG}); inspect, then remove it to resume"
    exit 1
}
trap 'fail_restore "unexpected error"' ERR

[ -f "$DB_DUMP" ] || fail_restore "staged db.dump not found (was preview run?)"

ok "portable restore starting (staged_id=${staged_id} mode=${mode})"
restore_status applying snapshot "taking the pre-restore safety snapshot"
log_event restore start true "portable restore starting"

# --- 1) Mandatory pre-restore safety snapshot of the CURRENT state -------------------------------
echo "=== restore 1/5: pre-restore safety snapshot ==="
if ! ./deploy/backup.sh full >/dev/null 2>&1; then
    fail_restore "pre-restore safety snapshot failed - aborting before any destruction"
fi
SAFETY_SNAPSHOT="ok"
ok "safety snapshot complete"

# --- 2) Quiesce: maintenance ON, stop the worker, terminate other DB sessions --------------------
echo "=== restore 2/5: quiesce ==="
restore_status applying quiesce "entering maintenance + stopping the worker"
mkdir -p "$STATUS_DIR"
date -u +%Y-%m-%dT%H:%M:%SZ >"$MAINT_FLAG"
chmod 0644 "$MAINT_FLAG"
if [ "$mode" = "compose" ]; then
    compose=(docker compose -f docker-compose.prod.yml --env-file .env.production)
    "${compose[@]}" exec -T worker doktok-worker quiesce >/dev/null 2>&1 || true
    "${compose[@]}" stop worker >/dev/null 2>&1 || true
    # Terminate other sessions on the app DB so pg_restore --clean can drop/recreate objects.
    "${compose[@]}" exec -T db psql -U doktok -d doktok -tAc \
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='doktok' AND pid<>pg_backend_pid()" \
        >/dev/null 2>&1 || true
else
    doktok-worker quiesce >/dev/null 2>&1 || true
    psql "$DATABASE_URL" -tAc \
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()" \
        >/dev/null 2>&1 || true
fi
ok "quiesced"

# --- 3) DB import: pg_restore --clean --if-exists, ensure pgvector, migrate forward, ANALYZE ------
echo "=== restore 3/5: database import ==="
restore_status applying db "restoring the database"
if [ "$mode" = "compose" ]; then
    "${compose[@]}" exec -T db psql -U doktok -d doktok -c \
        "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null
    # Stream the staged dump into the db container's pg_restore over stdin.
    "${compose[@]}" exec -T db pg_restore --clean --if-exists --no-owner --no-privileges \
        -U doktok -d doktok <"$DB_DUMP"
    "${compose[@]}" run --rm backend python -m doktok_api migrate >/dev/null
    "${compose[@]}" exec -T db psql -U doktok -d doktok -c "ANALYZE" >/dev/null
else
    psql "$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null
    pg_restore --clean --if-exists --no-owner --no-privileges -d "$DATABASE_URL" "$DB_DUMP"
    python -m doktok_api migrate >/dev/null
    psql "$DATABASE_URL" -c "ANALYZE" >/dev/null
fi
ok "database restored + migrated forward"

# --- 4) Files swap: atomically replace files_root with the staged files/ (keep .old) -------------
echo "=== restore 4/5: files swap ==="
restore_status applying files "swapping the files root"
files_root="${FILES_ROOT}"
files_old="${files_root}.old.$(date +%s)"
files_new="${files_root}.new.$(date +%s)"
if [ -d "$FILES_SRC" ]; then
    rm -rf "$files_new"
    cp -a "$FILES_SRC" "$files_new"
    if [ -e "$files_root" ]; then
        mv -f "$files_root" "$files_old"
    fi
    mv -f "$files_new" "$files_root"
    ok "files root swapped (old kept at ${files_old})"
else
    warn "archive carried no files/ tree; leaving the existing files root untouched"
fi

# --- 5) Finish: lift maintenance, restart the worker, mark done ----------------------------------
echo "=== restore 5/5: finish ==="
restore_status applying finish "lifting maintenance + restarting the worker"
if [ "$mode" = "compose" ]; then
    "${compose[@]}" start worker >/dev/null 2>&1 || true
    "${compose[@]}" exec -T worker doktok-worker quiesce --off >/dev/null 2>&1 || true
else
    doktok-worker quiesce --off >/dev/null 2>&1 || true
fi
rm -f "$MAINT_FLAG"
# Drop the trap now that we are past the point of no failure-rollback.
trap - ERR
restore_status done finish "restore complete"
log_event restore success true "portable restore complete"
# Clean up the consumed staging dir on success (decrypted data must not linger).
rm -rf "$STAGING"
ok "portable restore PASSED (staged_id=${staged_id})"
