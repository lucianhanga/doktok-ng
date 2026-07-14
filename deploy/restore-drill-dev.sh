#!/usr/bin/env bash
#
# make restore-drill-dev - DESTRUCTIVE dev disaster drill.
#
# The real "delete the database and bring it back" test, on your actual dev stack:
#   1. record baseline row counts + file count,
#   2. BACK UP  - pg_dump (custom) the live dev DB to ./backups/dev-drill + tar files_root
#      (aborts before wiping if the backup fails, so you are never left with nothing),
#   3. WIPE     - DROP + CREATE the dev database (all data gone) and empty files_root,
#   4. RESTORE  - pg_restore the dump + un-tar the files,
#   5. VERIFY   - assert the post-restore counts match the pre-wipe baseline.
#
# It only ever targets the LOCAL dev container (`doktok-db`), takes a kept backup first, and asks
# for confirmation (skip with FORCE=1). Unlike `make verify-recovery` (which restores into a
# throwaway and never touches your data) this genuinely wipes and rebuilds the real dev database -
# so stop `make run-backend` / `make run-worker` first (their connections are dropped on the wipe).
#
# Same-PostgreSQL-version, same-schema restore (what you backed up is what you get back). Restoring
# an OLDER backup under NEWER code is also safe: the backend's startup migration runner applies any
# pending migrations to the restored schema (forward-only) - see docs/operations/backup-and-recovery.md.
set -euo pipefail
cd "$(dirname "$0")/.."

G=$'\e[0;32m'; R=$'\e[0;31m'; Y=$'\e[0;33m'; N=$'\e[0m'
ok() { echo "${G}$*${N}"; }
warn() { echo "${Y}$*${N}"; }
err() { echo "${R}$*${N}" >&2; }

DB_CONTAINER="${DOKTOK_DB_CONTAINER:-doktok-db}"
FILES_ROOT="${DOKTOK_FILES_ROOT:-storage/files}"
DRILL_DIR="${DOKTOK_DRILL_DIR:-./backups/dev-drill}"

TABLES=(
  documents document_entities document_chunks extracted_records
  kg_entities kg_edges kg_entity_mentions kg_edge_provenance
  categories ingestion_jobs chat_threads chat_messages document_activity app_settings
)

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

_live() { docker exec -e PGPASSWORD=doktok "$DB_CONTAINER" psql -U doktok -d doktok -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }

if [ "${FORCE:-0}" != "1" ]; then
  echo "${Y}This DELETES all data in the dev database ('$DB_CONTAINER') and $FILES_ROOT."
  echo "A backup is taken first (kept under $DRILL_DIR). Stop run-backend/run-worker before continuing.${N}"
  read -r -p "Type 'wipe' to proceed: " ans
  [ "$ans" = "wipe" ] || { warn "aborted - nothing changed"; exit 1; }
fi

# 1. baseline counts (parallel indexed arrays - macOS ships bash 3.2, no associative arrays)
present=()
base_counts=()
for t in "${TABLES[@]}"; do
  [ "$(_live "SELECT to_regclass('public.$t') IS NOT NULL")" = "t" ] || continue
  present+=("$t")
  base_counts+=("$(_live "SELECT count(*) FROM $t")")
done
base_files="$(find "$FILES_ROOT" -type f 2>/dev/null | wc -l | tr -d ' ')"

# 2. BACK UP (abort before the wipe if this fails)
stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DRILL_DIR"
DUMP="$DRILL_DIR/dev-$stamp.dump"
FILESTAR="$DRILL_DIR/dev-$stamp-files.tgz"
echo "=== 1/4 back up the live dev DB + files -> $DRILL_DIR ==="
docker exec -e PGPASSWORD=doktok "$DB_CONTAINER" pg_dump -U doktok -d doktok --format=custom >"$DUMP"
[ -s "$DUMP" ] || { err "backup dump is empty - ABORTING before wipe"; exit 1; }
tar czf "$FILESTAR" -C "$FILES_ROOT" . 2>/dev/null || true
ok "backed up $(wc -c <"$DUMP" | tr -d ' ') bytes DB + $base_files files (kept for safety)"

# 3. WIPE - drop and recreate the database, empty files_root
echo "=== 2/4 WIPE: drop + recreate the dev database and empty files_root ==="
docker exec -e PGPASSWORD=doktok "$DB_CONTAINER" psql -U doktok -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE doktok WITH (FORCE);" -c "CREATE DATABASE doktok OWNER doktok;" >/dev/null
find "$FILES_ROOT" -mindepth 1 -delete 2>/dev/null || true
wiped="$(_live "SELECT count(*) FROM documents" 2>/dev/null || echo "n/a")"
ok "database recreated empty (documents now: ${wiped:-0}), files_root cleared"

# 4. RESTORE
echo "=== 3/4 restore from the backup ==="
docker cp "$DUMP" "$DB_CONTAINER:/tmp/restore.dump"
docker exec "$DB_CONTAINER" pg_restore -U doktok -d doktok --no-owner /tmp/restore.dump \
  >/dev/null 2>&1 || warn "pg_restore reported warnings (the assertions below are what matter)"
docker exec "$DB_CONTAINER" rm -f /tmp/restore.dump 2>/dev/null || true
tar xzf "$FILESTAR" -C "$FILES_ROOT" 2>/dev/null || true
ok "restored"

# 5. VERIFY: post-restore counts match the pre-wipe baseline
echo "=== 4/4 verify documents + enriched/extracted rows came back ==="
fail=0
i=0
for t in "${present[@]}"; do
  want="${base_counts[$i]:-0}"
  got="$(_live "SELECT count(*) FROM $t")"; got="${got:-0}"
  if [ "$want" = "$got" ]; then
    printf "  %s%-22s before=%-7s after=%-7s ok%s\n" "$G" "$t" "$want" "$got" "$N"
  else
    printf "  %s%-22s before=%-7s after=%-7s MISMATCH%s\n" "$R" "$t" "$want" "$got" "$N"
    fail=1
  fi
  i=$((i + 1))
done
after_files="$(find "$FILES_ROOT" -type f 2>/dev/null | wc -l | tr -d ' ')"
if [ "$base_files" = "$after_files" ]; then
  ok "  files_root: $base_files -> $after_files restored"
else
  err "  files_root MISMATCH: $base_files -> $after_files"
  fail=1
fi

echo
if [ "$fail" = 0 ]; then
  ok "DRILL PASSED: wiped the dev database + files and restored every checked table and all files from the backup."
  echo "Backup kept at: $DUMP (+ $FILESTAR). Restart run-backend/run-worker to reconnect."
else
  err "DRILL FAILED - counts do not match. Your backup is safe at: $DUMP (+ $FILESTAR)."
  err "Restore it by hand: docker exec -i $DB_CONTAINER pg_restore -U doktok -d doktok --clean --if-exists --no-owner < $DUMP"
  exit 1
fi
