#!/usr/bin/env bash
#
# Weekly LOGICAL safety-net dump (M12 #355). pgBackRest physical+WAL is the low-RPO primary; this is
# a portable, corruption- and cross-major-version-independent escape hatch (custom-format pg_dump)
# kept alongside it. Staged into the local repo folder; pruned to the last few.
#
# Env: DOKTOK_DATABASE_URL, DOKTOK_BACKUP_DIR. Run weekly via a systemd timer.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

require pg_dump
trap 'err "logical pg_dump FAILED"; exit 1' ERR

out="${PG_DIR}/logical"
mkdir -p "$out"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
dump="${out}/doktok-${ts}.dump"

echo "logical pg_dump (custom format) -> ${dump}"
pg_dump --format=custom --clean --if-exists -d "$DATABASE_URL" -f "$dump"
# Keep the last 4 weekly dumps.
ls -1t "$out"/doktok-*.dump 2>/dev/null | tail -n +5 | xargs -r rm -f

ok "logical dump complete ($(du -h "$dump" | cut -f1))"
