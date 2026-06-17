#!/usr/bin/env bash
#
# Postgres backup into the local pgBackRest repository (M12 #347/DB-A1 local half). pgBackRest gives
# incremental/differential/full base backups + continuous WAL archiving + one-command PITR; the repo
# is staged in a local folder ($DOKTOK_BACKUP_DIR/pg) and shipped offsite later by azure-sync.sh.
#
# The folder-based WAL+base+PITR mechanism this wraps is proven in deploy/test-pitr.sh.
# pgBackRest does an ONLINE backup, so it needs the Postgres server running with its archive_command
# set to `pgbackrest --stanza=doktok archive-push %p` (wired into the db container on the box, DB-A1).
#
# Env: DOKTOK_PGDATA (the data dir pgBackRest reads), DOKTOK_PGBACKREST_CIPHER_PASS (repo encryption,
#      store OFF the box), DOKTOK_BACKUP_DIR (default ./backups). Arg 1: full|diff|incr (default incr).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

require pgbackrest
: "${DOKTOK_PGDATA:?set DOKTOK_PGDATA (the Postgres data directory pgBackRest can read)}"
: "${DOKTOK_PGBACKREST_CIPHER_PASS:?set DOKTOK_PGBACKREST_CIPHER_PASS (and store it off the box)}"
trap 'write_status pg false "backup failed"; err "postgres backup FAILED"; exit 1' ERR

mkdir -p "$PG_DIR"
conf="${BACKUP_DIR}/pgbackrest.conf"
cat >"$conf" <<CONF
[global]
repo1-path=$(cd "$PG_DIR" && pwd)
repo1-retention-full=${DOKTOK_PG_RETENTION_FULL:-2}
repo1-cipher-type=aes-256-cbc
repo1-cipher-pass=${DOKTOK_PGBACKREST_CIPHER_PASS}
compress-type=lz4
process-max=${DOKTOK_PG_PROCESS_MAX:-2}
start-fast=y

[doktok]
pg1-path=${DOKTOK_PGDATA}
CONF
chmod 600 "$conf"

type="${1:-incr}"
pgbackrest --config="$conf" --stanza=doktok stanza-create 2>/dev/null || true
pgbackrest --config="$conf" --stanza=doktok check
pgbackrest --config="$conf" --stanza=doktok backup --type="$type"
write_status pg true "pgbackrest ${type}"
ok "pgBackRest ${type} backup complete -> ${PG_DIR}"
pgbackrest --config="$conf" --stanza=doktok info
