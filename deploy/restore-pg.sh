#!/usr/bin/env bash
#
# Postgres restore from the local pgBackRest repository (M12). DESTRUCTIVE: restores into
# DOKTOK_PGDATA (the server must be STOPPED). Optionally point-in-time via arg 1.
#
# Usage:  ./deploy/restore-pg.sh ["YYYY-MM-DD HH:MM:SS+00"]   (omit for latest)
# Env: DOKTOK_PGDATA, DOKTOK_PGBACKREST_CIPHER_PASS, DOKTOK_BACKUP_DIR.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

require pgbackrest
: "${DOKTOK_PGDATA:?set DOKTOK_PGDATA}"
: "${DOKTOK_PGBACKREST_CIPHER_PASS:?set DOKTOK_PGBACKREST_CIPHER_PASS}"
trap 'err "postgres restore FAILED"; exit 1' ERR

conf="${BACKUP_DIR}/pgbackrest.conf"
if [ ! -f "$conf" ]; then
    mkdir -p "$(dirname "$conf")"
    cat >"$conf" <<CONF
[global]
repo1-path=$(cd "$PG_DIR" && pwd)
repo1-cipher-type=aes-256-cbc
repo1-cipher-pass=${DOKTOK_PGBACKREST_CIPHER_PASS}

[doktok]
pg1-path=${DOKTOK_PGDATA}
CONF
    chmod 600 "$conf"
fi

# Version-match guard (#356): a physical restore requires the SAME PostgreSQL major version, and the
# pgvector extension must be present/compatible on the target (the logical dump is the only
# cross-major path; HNSW is rebuilt only on that logical path, not here).
db_ver="$(pgbackrest --config="$conf" --stanza=doktok info --output=json 2>/dev/null \
    | grep -oE '"version"[ :]*"[0-9]+' | grep -oE '[0-9]+$' | head -1 || true)"
if [ -n "$db_ver" ] && [ "$db_ver" != "17" ]; then
    err "backup is PostgreSQL ${db_ver}; the restore target must be the SAME major (pg17). Aborting."
    exit 1
fi
warn "ensure the target image is pgvector/pgvector:pg17 (matching pgvector); physical restore is not cross-version"

target="${1:-}"
args=(--config="$conf" --stanza=doktok restore --delta)
if [ -n "$target" ]; then
    warn "point-in-time restore to: $target"
    args+=(--type=time "--target=$target" --target-action=promote)
fi
warn "restoring into $DOKTOK_PGDATA (the Postgres server must be stopped). Ctrl-C within 5s to abort."
sleep 5
pgbackrest "${args[@]}"
ok "pgBackRest restore complete. Start Postgres; it will replay WAL and (for PITR) promote."
