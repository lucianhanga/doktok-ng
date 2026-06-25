#!/usr/bin/env bash
#
# Self-contained proof that folder-based WAL archiving + base backup + point-in-time recovery works
# (M12). Uses throwaway pgvector:pg17 containers and a local WAL/base folder - it touches NO existing
# database. It is the mechanism pgBackRest (backup-pg.sh / restore-pg.sh) wraps for production.
#
# It inserts row A, takes a base backup, records T1, inserts row B, then restores to T1 and asserts
# the restore contains ONLY A. Requires Docker. Cleans up after itself.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh
require docker

T="$(mktemp -d)"
mkdir -p "$T/wal" "$T/base" "$T/restore"
chmod -R 777 "$T"
cleanup() { docker rm -f doktok-pitr-a doktok-pitr-b >/dev/null 2>&1 || true; rm -rf "$T"; }
trap cleanup EXIT
docker rm -f doktok-pitr-a doktok-pitr-b >/dev/null 2>&1 || true

echo "starting primary with WAL archiving -> $T/wal"
docker run -d --name doktok-pitr-a -e POSTGRES_USER=doktok -e POSTGRES_PASSWORD=x -e POSTGRES_DB=doktok \
    -v "$T/wal":/wal -v "$T/base":/base \
    pgvector/pgvector:pg17 \
    -c wal_level=replica -c archive_mode=on -c "archive_command=test ! -f /wal/%f && cp %p /wal/%f" >/dev/null
for _ in $(seq 1 30); do docker exec doktok-pitr-a pg_isready -U doktok >/dev/null 2>&1 && break; sleep 1; done

docker exec doktok-pitr-a psql -U doktok -d doktok -c "CREATE TABLE t(n text);" -c "INSERT INTO t VALUES('A');" >/dev/null
docker exec doktok-pitr-a bash -c "rm -rf /base/* && pg_basebackup -U doktok -D /base -X stream" >/dev/null 2>&1
sleep 1
t1="$(docker exec doktok-pitr-a psql -U doktok -d doktok -tAc 'SELECT now();')"
sleep 2
docker exec doktok-pitr-a psql -U doktok -d doktok -c "INSERT INTO t VALUES('B');" -c "SELECT pg_switch_wal();" >/dev/null
sleep 2

cp -a "$T/base/." "$T/restore/"
: >"$T/restore/recovery.signal"
{
    echo "restore_command = 'cp /wal/%f %p'"
    echo "recovery_target_time = '$t1'"
    echo "recovery_target_action = 'promote'"
} >>"$T/restore/postgresql.auto.conf"
chmod -R 777 "$T/restore"

docker run -d --name doktok-pitr-b -e POSTGRES_PASSWORD=x \
    -v "$T/wal":/wal -v "$T/restore":/var/lib/postgresql/data \
    pgvector/pgvector:pg17 >/dev/null
for _ in $(seq 1 40); do docker exec doktok-pitr-b pg_isready -U doktok >/dev/null 2>&1 && break; sleep 1; done
sleep 3

got="$(docker exec doktok-pitr-b psql -U doktok -d doktok -tAc "SELECT string_agg(n,',' ORDER BY n) FROM t;")"
if [ "$got" = "A" ]; then
    ok "PITR proof PASSED: restore to T1 contains only 'A' (B correctly excluded)"
else
    err "PITR proof FAILED: expected 'A', got '$got'"
    exit 1
fi
