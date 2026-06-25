#!/usr/bin/env bash
#
# Per-minute Postgres WAL recovery-point stamp for the DRP (M12 #377).
#
# The pg leg's RPO is ~60s, held by continuous WAL archiving (archive_command -> pgBackRest
# archive-push, with archive_timeout=60 so even an idle DB ships a segment each minute). But a base
# backup only runs hourly/weekly, so if the pg sentinel's freshness came from the base backup alone
# the leg would flap "stale" between base backups. This stamps the sentinel's last_run_at = the last
# archived WAL time (the real recovery point) and records the WAL lag, so the DRP derives ok/stale
# from the actual archiving lag. The base backup's size/backup_id metrics (written by backup-pg.sh)
# are read back and preserved.
#
# Mode-aware like backup.sh: compose -> query via `docker compose exec db psql`; host -> local psql.
# The sentinel is written directly into $DOKTOK_BACKUP_DIR/status (a host path in both topologies),
# so this needs no container to write. Best-effort: if Postgres is unreachable it leaves the existing
# sentinel untouched (it will age and go stale on its own, which is the correct signal).
#
# Env: DOKTOK_DEPLOY_MODE (host|compose), DOKTOK_BACKUP_DIR, DOKTOK_DATABASE_URL (host mode).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

mode="${DOKTOK_DEPLOY_MODE:-host}"

# pg_stat_archiver in one row: last archived epoch | lag seconds | failed_count | seconds since last
# failure. A pipe-joined scalar keeps the parse trivial (no jq in the runner / on a minimal host).
sql="SELECT COALESCE(EXTRACT(EPOCH FROM last_archived_time)::bigint, 0)
  || '|' || COALESCE(EXTRACT(EPOCH FROM (now() - last_archived_time))::bigint, -1)
  || '|' || COALESCE(failed_count, 0)
  || '|' || COALESCE(EXTRACT(EPOCH FROM (now() - last_failed_time))::bigint, 999999)
  FROM pg_stat_archiver;"

if [ "$mode" = "compose" ]; then
    compose=(docker compose -f docker-compose.prod.yml --env-file .env.production)
    row="$("${compose[@]}" exec -T db psql -U doktok -d doktok -tAc "$sql" 2>/dev/null | tr -d '[:space:]' || true)"
else
    row="$(psql "$DATABASE_URL" -tAc "$sql" 2>/dev/null | tr -d '[:space:]' || true)"
fi

if [ -z "$row" ]; then
    warn "pg WAL-freshness: Postgres unreachable; leaving the pg sentinel untouched"
    exit 0
fi

arch_epoch="${row%%|*}"
rest="${row#*|}"
lag="${rest%%|*}"
rest="${rest#*|}"
failed_count="${rest%%|*}"
failed_age="${rest##*|}"

if [ "${lag}" = "-1" ] || [ "${arch_epoch}" = "0" ]; then
    warn "pg WAL-freshness: no WAL archived yet (run a pg base backup first); skipping"
    exit 0
fi

# ISO recovery point from the archived-WAL epoch. GNU date (Linux/box) then BSD date (mac dev).
ts="$(date -u -d "@${arch_epoch}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
    || date -u -r "${arch_epoch}" +%Y-%m-%dT%H:%M:%SZ)"

# Preserve the last base backup's metrics from the existing sentinel (size + label).
prev="${STATUS_DIR}/pg.json"
psize=""
pid=""
if [ -f "$prev" ]; then
    # `|| true`: the field may be absent (e.g. before the first base backup); pipefail would abort.
    psize="$(grep -oE '"size":"[^"]*"' "$prev" | head -1 | sed -E 's/"size":"(.*)"/\1/' || true)"
    pid="$(grep -oE '"backup_id":"[^"]*"' "$prev" | head -1 | sed -E 's/"backup_id":"(.*)"/\1/' || true)"
fi

# Healthy unless the most recent archive attempt failed (last failure newer than last success).
ok=true
[ "${failed_count}" -gt 0 ] && [ "${failed_age}" -lt "${lag}" ] && ok=false

extra="\"wal_lag_s\":${lag},\"size\":\"${psize}\",\"backup_id\":\"${pid}\""
WRITE_STATUS_TS="$ts" write_status pg "$ok" "WAL archiving (base: ${pid:-none})" "$extra"
ok "pg WAL recovery point stamped: ${ts} (lag ${lag}s, ok=${ok})"
