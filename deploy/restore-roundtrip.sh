#!/usr/bin/env bash
#
# Self-contained proof that a portable backup made on one instance RESTORES onto a DIFFERENT one
# (the cross-device DR claim). Uses throwaway pgvector:pg17 containers + a temp files tree, mirrors
# test-pitr.sh: it touches NO existing database/files and cleans up after itself. Requires Docker.
#
# A: seed a table (incl. a pgvector column) + rows + a file -> pg_dump (custom) + tar the files ->
#    openssl-encrypt the bundle with a passphrase -> decrypt -> pg_restore into a FRESH instance B +
#    unpack the files -> assert the rows, the vector value, and the file content came across exactly.
# This exercises the same mechanism the app's export/restore uses (pg_dump custom, openssl AES-256,
# pg_restore --clean, files swap), without needing a running stack or systemd.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh
require docker

T="$(mktemp -d)"
mkdir -p "$T/files-a" "$T/files-b" "$T/bundle" "$T/restore"
chmod -R 777 "$T"
A=doktok-rt-a
B=doktok-rt-b
IMG=pgvector/pgvector:pg17
PASS="drp-selftest-pass"  # pragma: allowlist secret  (throwaway, local-only)

cleanup() { docker rm -f "$A" "$B" >/dev/null 2>&1 || true; rm -rf "$T"; }
trap cleanup EXIT
docker rm -f "$A" "$B" >/dev/null 2>&1 || true

_wait_ready() { for _ in $(seq 1 30); do docker exec "$1" pg_isready -U doktok >/dev/null 2>&1 && return 0; sleep 1; done; err "$1 did not become ready"; return 1; }

echo "starting source instance A"
docker run -d --name "$A" -e POSTGRES_USER=doktok -e POSTGRES_PASSWORD=x -e POSTGRES_DB=doktok \
    -v "$T":/work "$IMG" >/dev/null
_wait_ready "$A"

echo "seeding A: a table with a pgvector column + rows + a document file"
docker exec "$A" psql -U doktok -d doktok -v ON_ERROR_STOP=1 \
    -c "CREATE EXTENSION IF NOT EXISTS vector;" \
    -c "CREATE TABLE doc (id int primary key, body text, emb vector(3));" \
    -c "INSERT INTO doc VALUES (1,'alpha','[1,2,3]'),(2,'beta','[4,5,6]'),(3,'gamma','[7,8,9]');" >/dev/null
printf 'hello-drp\n' >"$T/files-a/note.txt"

echo "exporting from A: pg_dump (custom) + files tar -> encrypted bundle"
docker exec "$A" pg_dump -U doktok -d doktok --format=custom --file=/work/bundle/db.dump
tar -C "$T/files-a" -czf "$T/bundle/files.tgz" .
tar -C "$T/bundle" -czf "$T/archive.tgz" db.dump files.tgz
printf '%s' "$PASS" | openssl enc -aes-256-cbc -pbkdf2 -salt -pass stdin -in "$T/archive.tgz" -out "$T/archive.tgz.enc"
ok "encrypted archive built ($(wc -c <"$T/archive.tgz.enc" | tr -d ' ') bytes)"

echo "decrypting + unpacking onto target B"
printf '%s' "$PASS" | openssl enc -d -aes-256-cbc -pbkdf2 -salt -pass stdin -in "$T/archive.tgz.enc" -out "$T/archive.dec.tgz"
tar -C "$T/restore" -xzf "$T/archive.dec.tgz"
tar -C "$T/files-b" -xzf "$T/restore/files.tgz"

echo "starting fresh target instance B + restoring the DB"
docker run -d --name "$B" -e POSTGRES_USER=doktok -e POSTGRES_PASSWORD=x -e POSTGRES_DB=doktok \
    -v "$T":/work "$IMG" >/dev/null
_wait_ready "$B"
docker exec "$B" pg_restore -U doktok -d doktok --clean --if-exists --no-owner /work/restore/db.dump \
    >/dev/null 2>&1 || warn "pg_restore reported warnings (checking the data is what matters)"

echo "verifying B matches A"
rows="$(docker exec "$B" psql -U doktok -d doktok -tAc 'SELECT count(*) FROM doc;' | tr -d '[:space:]')"
emb="$(docker exec "$B" psql -U doktok -d doktok -tAc 'SELECT emb FROM doc WHERE id=2;' | tr -d '[:space:]')"
file="$(tr -d '[:space:]' <"$T/files-b/note.txt" 2>/dev/null || true)"
fail=0
[ "$rows" = "3" ] || { err "row count mismatch: got '$rows', want 3"; fail=1; }
[ "$emb" = "[4,5,6]" ] || { err "pgvector value mismatch: got '$emb', want [4,5,6]"; fail=1; }
[ "$file" = "hello-drp" ] || { err "file content mismatch: got '$file', want 'hello-drp'"; fail=1; }
[ "$fail" = 0 ] || { err "restore round-trip FAILED"; exit 1; }
ok "restore round-trip PASSED: 3 rows + pgvector value + document file restored A -> B"
