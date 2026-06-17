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
