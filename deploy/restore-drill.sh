#!/usr/bin/env bash
#
# Scheduled restore drill (M12 DEVOPS-D3 + DRP hardening, upgraded #755): prove the backups
# actually restore EVERYTHING, into throwaway locations, and record the result in BOTH the drill
# sentinel (latest-state, for /drp) and the append-only history (drill_pass/drill_fail). An
# untested backup is not a backup. Run weekly via a systemd timer (or on demand via the
# request-file path unit); alert on failure.
#
# NO production data is touched: the files snapshot is restored into a temp dir, the pg backup
# into a throwaway container. Verification (lib-drill-verify.sh):
#   files: restored file count == live files_root count, plus sha256 spot-checks of restored
#          originals against documents.sha256;
#   pg:    the latest pgBackRest backup restored into a throwaway container (archive replay to
#          end-of-WAL), then per-table row counts compared EXACTLY against the live DB.
# It also records measured RPO (now - latest archived recovery point) and RTO (wall-clock).
# Note: run when ingestion is idle - the restored DB lags the live one by up to the WAL interval.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh
source deploy/lib-drill-verify.sh

mode="${DOKTOK_DEPLOY_MODE:-compose}"
COMPOSE_FILES="${DOKTOK_COMPOSE_FILES:-docker-compose.prod.yml}"
COMPOSE_ENV_FILE="${DOKTOK_COMPOSE_ENV_FILE:-.env.production}"
compose=(docker compose)
for f in ${COMPOSE_FILES//,/ }; do
    compose+=(-f "$f")
done
compose+=(--env-file "$COMPOSE_ENV_FILE")

drill_t0="$(date +%s)"
# date +%s%3N is GNU-only; on BSD (macOS) it exits 0 with a literal 'N' suffix, so validate.
drill_t0_ms="$(date +%s%3N 2>/dev/null || true)"
case "$drill_t0_ms" in ''|*[!0-9]*) drill_t0_ms=0 ;; esac

fail_drill() {
    local msg="$1"
    write_status drill false "drill failed: ${msg}"
    log_event drill drill_fail false "drill failed: ${msg}"
    err "restore drill FAILED: ${msg}"
    exit 1
}
trap 'fail_drill "unexpected error"' ERR

tmp="$(mktemp -d)"
drill_pg_container="doktok-drill-pg-$$"
drill_pg_volume="doktok-drill-pgdata-$$"
cleanup() {
    docker rm -f "$drill_pg_container" >/dev/null 2>&1 || true
    docker volume rm "$drill_pg_volume" >/dev/null 2>&1 || true
    rm -rf "$tmp"
}
trap 'cleanup' EXIT

echo "=== drill 1/2: files snapshot restore + verify ==="
if [ "$mode" = "compose" ]; then
    # restic lives in the backup-runner container; mount the throwaway target in.
    mkdir -p "$tmp/files"
    "${compose[@]}" run --rm -v "$tmp/files:/restore-out" backup-runner \
        deploy/restore-files.sh /restore-out >/dev/null
else
    DOKTOK_BACKUP_DIR="$BACKUP_DIR" ./deploy/restore-files.sh "$tmp/files" >/dev/null
fi
restored_count="$(find "$tmp/files" -type f ! -name '.DS_Store' | wc -l | tr -d ' ')"
live_count="$(find "$FILES_ROOT" -type f ! -name '.DS_Store' 2>/dev/null | wc -l | tr -d ' ')"
[ "$restored_count" -gt 0 ] || fail_drill "files restore produced no files"
[ "$restored_count" = "$live_count" ] || \
    fail_drill "restored files ($restored_count) != live files_root ($live_count)"

# sha256 spot-check: restored original bytes vs the DB's documents.sha256 (the manifest).
if [ "$mode" = "compose" ]; then
    sha_provider="${compose[*]} exec -T db psql -U doktok -d doktok -tAc \"SELECT id || '|' || sha256 FROM documents\""
else
    sha_provider="psql '${DATABASE_URL}' -tAc \"SELECT id || '|' || sha256 FROM documents\""
fi
drill_verify_hashes "$tmp/files" "$sha_provider" 25 || fail_drill "original-byte hash check failed"
ok "files restore OK (${restored_count}/${live_count} files, hash sample ok)"

echo "=== drill 2/2: Postgres restore into throwaway + row-count compare ==="
if [ "$mode" != "compose" ]; then
    # Host mode has no db container/image to clone; fall back to the synthetic PITR proof.
    warn "host mode: full count verification needs compose mode; running the basic PITR proof"
    ./deploy/test-pitr.sh >/dev/null
    ok "Postgres PITR proof OK (basic)"
    rows_evidence="basic"
else
    cipher="$(grep '^DOKTOK_PGBACKREST_CIPHER_PASS=' "$COMPOSE_ENV_FILE" | cut -d= -f2- || true)"
    [ -n "$cipher" ] || fail_drill "DOKTOK_PGBACKREST_CIPHER_PASS not in $COMPOSE_ENV_FILE"
    db_image="$(docker inspect doktok-db --format '{{.Config.Image}}' 2>/dev/null || true)"
    [ -n "$db_image" ] || fail_drill "cannot find the running doktok-db container (image source)"
    repo_abs="$(cd "${BACKUP_DIR}/pg" && pwd)"
    conf_abs="$(cd deploy/pgbackrest && pwd)/pgbackrest.conf"

    docker volume create "$drill_pg_volume" >/dev/null
    docker run --rm -u postgres \
        -e "PGBACKREST_REPO1_CIPHER_PASS=$cipher" \
        -v "$repo_abs:/var/lib/doktok/pg" \
        -v "$conf_abs:/etc/pgbackrest/pgbackrest.conf:ro" \
        -v "$drill_pg_volume:/var/lib/postgresql/data" \
        --entrypoint pgbackrest "$db_image" --stanza=doktok restore --delta >/dev/null \
        || fail_drill "pgbackrest throwaway restore failed"
    docker run -d --name "$drill_pg_container" \
        -e POSTGRES_PASSWORD=x -e "PGBACKREST_REPO1_CIPHER_PASS=$cipher" \
        -v "$repo_abs:/var/lib/doktok/pg" \
        -v "$conf_abs:/etc/pgbackrest/pgbackrest.conf:ro" \
        -v "$drill_pg_volume:/var/lib/postgresql/data" \
        "$db_image" postgres -c archive_mode=off >/dev/null
    # Wait for archive recovery + promotion (end-of-WAL) before comparing.
    ready=0
    for _ in $(seq 1 60); do
        if docker exec "$drill_pg_container" pg_isready -U doktok -d doktok >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 2
    done
    [ "$ready" = "1" ] || fail_drill "throwaway postgres never became ready"

    live_counts="$(drill_db_counts compose)"
    restored_counts="$(drill_db_counts_container "$drill_pg_container")"
    drill_counts_match "$live_counts" "$restored_counts" || \
        fail_drill "restored DB row counts != live (idle the ingestion and re-run)"
    ok "Postgres restore OK (row counts match live: ${restored_counts})"
    rows_evidence="$restored_counts"
fi

# Measured RPO: how far behind the latest recovery point is. Prefer the pg WAL-freshness sentinel's
# last_run_at (the last archived WAL time = the real recovery point); fall back to 0 when absent.
rpo_seconds=0
pg_sentinel="${STATUS_DIR}/pg.json"
if [ -f "$pg_sentinel" ]; then
    pg_rp="$(sed -n 's/.*"last_run_at":"\([^"]*\)".*/\1/p' "$pg_sentinel" | head -1)"
    if [ -n "$pg_rp" ]; then
        rp_epoch="$(date -u -d "$pg_rp" +%s 2>/dev/null || date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$pg_rp" +%s 2>/dev/null || echo 0)"
        [ "${rp_epoch:-0}" -gt 0 ] && rpo_seconds="$(( $(date +%s) - rp_epoch ))"
        [ "$rpo_seconds" -lt 0 ] && rpo_seconds=0
    fi
fi

# Measured RTO: wall-clock of the whole drill (proxy for time-to-recover).
rto_seconds="$(( $(date +%s) - drill_t0 ))"
drill_dur_ms=0
now_ms="$(date +%s%3N 2>/dev/null || true)"
case "$now_ms" in ''|*[!0-9]*) now_ms=0 ;; esac
[ "${drill_t0_ms:-0}" -gt 0 ] && [ "$now_ms" -gt 0 ] && drill_dur_ms="$(( now_ms - drill_t0_ms ))"

evidence="files=${restored_count}/${live_count} hashes=ok rows=[${rows_evidence}] rpo=${rpo_seconds}s rto=${rto_seconds}s"
write_status drill true "$evidence"
log_event drill drill_pass true "$evidence" \
    "\"item_count\":${restored_count},\"duration_ms\":${drill_dur_ms}"
ok "restore drill PASSED (${evidence})"
