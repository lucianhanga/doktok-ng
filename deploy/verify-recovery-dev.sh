#!/usr/bin/env bash
#
# Dev-only, NO-RISK recovery verification (`make verify-recovery`).
#
# Proves that the LIVE dev database + files_root round-trip through a backup/restore with your
# documents AND all enriched/extracted data intact. It:
#   1. dumps the live dev DB with the db container's own pg_dump (custom format),
#   2. restores that dump into a THROWAWAY Postgres container,
#   3. asserts the row count of every key table matches live -> restored,
#   4. tars files_root, re-extracts it, and asserts the file count survives.
#
# It touches NEITHER the live database NOR files_root - the dump is read-only and the restore lands
# in a disposable container that is torn down at the end. Requires only Docker (the lightweight dev
# stack, `make db`). It does NOT exercise the encrypted portable-backup archive or WAL/PITR - use
# `make drp-selftest` (mechanism proof) and the Settings -> DRP portable backup for those. This is
# the "did my real documents + enrichment come back" check to run after ingesting.
set -euo pipefail
cd "$(dirname "$0")/.."

G=$'\e[0;32m'; R=$'\e[0;31m'; Y=$'\e[0;33m'; N=$'\e[0m'
ok() { echo "${G}$*${N}"; }
warn() { echo "${Y}$*${N}"; }
err() { echo "${R}$*${N}" >&2; }

DB_CONTAINER="${DOKTOK_DB_CONTAINER:-doktok-db}"
FILES_ROOT="${DOKTOK_FILES_ROOT:-storage/files}"
PG_IMAGE="${DOKTOK_PG_IMAGE:-pgvector/pgvector:pg17}"
TARGET="doktok-recovery-check-$$"
WORK="$(mktemp -d)"

# documents + every enriched/extracted derivative whose count must survive a restore.
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

cleanup() { docker rm -f "$TARGET" >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT

_live() { docker exec -e PGPASSWORD=doktok "$DB_CONTAINER" psql -U doktok -d doktok -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }
_target() { docker exec -e PGPASSWORD=x "$TARGET" psql -U doktok -d doktok -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }

echo "=== 1/4 dump the live dev database (read-only, pg_dump custom) ==="
docker exec -e PGPASSWORD=doktok "$DB_CONTAINER" pg_dump -U doktok -d doktok --format=custom >"$WORK/db.dump"
ok "dumped $(wc -c <"$WORK/db.dump" | tr -d ' ') bytes"

echo "=== 2/4 restore into a throwaway Postgres ($PG_IMAGE) ==="
docker run -d --name "$TARGET" -e POSTGRES_USER=doktok -e POSTGRES_PASSWORD=x -e POSTGRES_DB=doktok "$PG_IMAGE" >/dev/null
for _ in $(seq 1 60); do docker exec "$TARGET" pg_isready -U doktok >/dev/null 2>&1 && break; sleep 1; done
docker cp "$WORK/db.dump" "$TARGET:/tmp/db.dump"
docker exec "$TARGET" pg_restore -U doktok -d doktok --clean --if-exists --no-owner /tmp/db.dump \
  >/dev/null 2>&1 || warn "pg_restore reported warnings (the row-count assertions below are what matter)"
ok "restored into throwaway container"

echo "=== 3/4 assert documents + enriched/extracted row counts survive ==="
fail=0
for t in "${TABLES[@]}"; do
  if [ "$(_live "SELECT to_regclass('public.$t') IS NOT NULL")" != "t" ]; then
    warn "  $(printf '%-22s' "$t") not in live schema - skipped"
    continue
  fi
  a="$(_live "SELECT count(*) FROM $t")"; a="${a:-0}"
  b="$(_target "SELECT count(*) FROM $t")"; b="${b:-0}"
  if [ "$a" = "$b" ]; then
    printf "  %s%-22s live=%-7s restored=%-7s ok%s\n" "$G" "$t" "$a" "$b" "$N"
  else
    printf "  %s%-22s live=%-7s restored=%-7s MISMATCH%s\n" "$R" "$t" "$a" "$b" "$N"
    fail=1
  fi
done

echo "=== 4/4 files_root round-trips ==="
live_files="$(find "$FILES_ROOT" -type f 2>/dev/null | wc -l | tr -d ' ')"
mkdir -p "$WORK/files-restored"
tar -C "$FILES_ROOT" -cf "$WORK/files.tar" . 2>/dev/null || true
tar -C "$WORK/files-restored" -xf "$WORK/files.tar" 2>/dev/null || true
rest_files="$(find "$WORK/files-restored" -type f 2>/dev/null | wc -l | tr -d ' ')"
if [ "$live_files" = "$rest_files" ]; then
  ok "  files_root: $live_files files -> $rest_files restored"
else
  err "  files_root MISMATCH: $live_files -> $rest_files"
  fail=1
fi

echo
if [ "$fail" = 0 ]; then
  ok "RECOVERY VERIFIED: the live dev database + files round-trip through a restore with every checked table and all files intact."
else
  err "RECOVERY CHECK FAILED - see the mismatches above."
  exit 1
fi
