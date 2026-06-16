#!/usr/bin/env bash
#
# Back up the DokTok NG production stack (M11 #330 / DEVOPS-6): the Postgres database and the
# files_root tree, the two things that hold state. Output: $BACKUP_DIR/<utc-timestamp>/ with
# db.sql.gz + files.tar.gz.
#
# The backup is SECRET-BEARING: it contains the OpenAI API key (in app_settings until APP-8) and
# document content. Store it encrypted and off the box.
#
# Usage:  BACKUP_DIR=/mnt/backups ./deploy/backup.sh
set -euo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; NC='\033[0m'
ok()   { printf "${GREEN}%s${NC}\n" "$*"; }
warn() { printf "${YELLOW}%s${NC}\n" "$*"; }
err()  { printf "${RED}%s${NC}\n" "$*" >&2; }
trap 'err "backup FAILED"; exit 1' ERR

COMPOSE=(docker compose -f docker-compose.prod.yml)
BACKUP_DIR="${BACKUP_DIR:-./backups}"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/$ts"
mkdir -p "$out"

warn "Backups contain the OpenAI key + document content - store them encrypted and off-box."

echo "Dumping Postgres -> $out/db.sql.gz"
"${COMPOSE[@]}" exec -T db pg_dump --clean --if-exists -U doktok -d doktok | gzip >"$out/db.sql.gz"

echo "Archiving files_root -> $out/files.tar.gz"
"${COMPOSE[@]}" run --rm --no-deps -T -v "$(pwd)/$out:/backup" --entrypoint sh worker \
  -c "tar czf /backup/files.tar.gz -C /data files"

ok "Backup complete: $out"
