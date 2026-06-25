# Shared helpers + config for the DokTok NG backup/restore scripts (M12).
# Source this from the other deploy/*.sh scripts. The design is local-first: everything is staged
# into a local repository folder ($DOKTOK_BACKUP_DIR), then pushed offsite by azure-sync.sh - so the
# whole backup/restore engine works and is testable without any cloud account.

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'
ok() { printf "${GREEN}%s${NC}\n" "$*"; }
warn() { printf "${YELLOW}%s${NC}\n" "$*"; }
err() { printf "${RED}%s${NC}\n" "$*" >&2; }

# Local staging repository root. Subdirs:
#   files/   restic repo for the files_root tree (dedup + AES-256)
#   pg/      Postgres base backups + archived WAL (point-in-time recovery)
BACKUP_DIR="${DOKTOK_BACKUP_DIR:-./backups}"
FILES_REPO="${BACKUP_DIR}/files"
PG_DIR="${BACKUP_DIR}/pg"
PG_BASE_DIR="${PG_DIR}/base"
PG_WAL_DIR="${PG_DIR}/wal"

# What we back up.
FILES_ROOT="${DOKTOK_FILES_ROOT:-./storage/files}"
DATABASE_URL="${DOKTOK_DATABASE_URL:-postgresql://doktok:doktok@localhost:5432/doktok}"  # pragma: allowlist secret

# Freshness sentinels (M12 DEVOPS-D1): one JSON file per backup leg, written outside the database
# (so a Postgres restore can't roll backup status back). The backend's DRP panel + /metrics read
# these. Per-leg files avoid concurrent-write races between legs.
STATUS_DIR="${BACKUP_DIR}/status"

require() {
    command -v "$1" >/dev/null 2>&1 || {
        err "required tool not found: $1"
        exit 1
    }
}

# write_status <leg> <true|false> [detail] - atomic per-leg freshness sentinel.
write_status() {
    local leg="$1" ok="$2" detail="${3:-}"
    mkdir -p "$STATUS_DIR"
    local tmp
    tmp="$(mktemp "${STATUS_DIR}/.${leg}.XXXXXX")"
    printf '{"leg":"%s","ok":%s,"last_run_at":"%s","detail":"%s"}\n' \
        "$leg" "$ok" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$detail" >"$tmp"
    mv -f "$tmp" "${STATUS_DIR}/${leg}.json"
    # Non-secret status; must be readable by the backend (a different uid in compose). (M12 #377)
    chmod 0644 "${STATUS_DIR}/${leg}.json"
}
