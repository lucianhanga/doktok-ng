#!/usr/bin/env bash
#
# Restore the DokTok NG production stack from a backup made by backup.sh (M11 #330 / DEVOPS-6).
# DESTRUCTIVE: overwrites the current database and files_root. Test this against a staging stack.
#
# Usage:  ./deploy/restore.sh <backup-dir>      (the dir containing db.sql.gz + files.tar.gz)
set -euo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; NC='\033[0m'
ok()   { printf "${GREEN}%s${NC}\n" "$*"; }
warn() { printf "${YELLOW}%s${NC}\n" "$*"; }
err()  { printf "${RED}%s${NC}\n" "$*" >&2; }
trap 'err "restore FAILED"; exit 1' ERR

src="${1:?usage: restore.sh <backup-dir>}"
[ -f "$src/db.sql.gz" ] || { err "no db.sql.gz in $src"; exit 1; }
[ -f "$src/files.tar.gz" ] || { err "no files.tar.gz in $src"; exit 1; }

COMPOSE=(docker compose -f docker-compose.prod.yml)

warn "This OVERWRITES the current database and files_root from $src. Ctrl-C within 5s to abort."
sleep 5

echo "Restoring Postgres (the dump drops + recreates objects via --clean --if-exists)..."
gunzip -c "$src/db.sql.gz" | "${COMPOSE[@]}" exec -T db psql -U doktok -d doktok

echo "Restoring files_root..."
"${COMPOSE[@]}" run --rm --no-deps -T -v "$(pwd)/$src:/backup" --entrypoint sh worker \
  -c "rm -rf /data/files && tar xzf /backup/files.tar.gz -C /data"

ok "Restore complete. Restart the stack: docker compose -f docker-compose.prod.yml up -d"
