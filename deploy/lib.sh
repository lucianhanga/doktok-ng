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

# pg_backup_extra - read `pgbackrest info --output=json` from stdin and emit a JSON metric fragment
# (size + backup_id) for write_status's 4th arg (M12 #380). Parsed with python3 (always on the host;
# the db container has none, so this runs host-side). Prints nothing if parsing fails.
pg_backup_extra() {
    command -v python3 >/dev/null 2>&1 || return 0
    python3 - <<'PY' || true
import sys, json
def human(n):
    n = float(n)
    for u in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or u == "TiB":
            return f"{n:.1f} {u}"
        n /= 1024
try:
    data = json.load(sys.stdin)
    backups = data[0]["backup"]
    if not backups:
        sys.exit(0)
    b = backups[-1]
    size = human(b["info"]["size"])
    label = b["label"]
    print(f'"size":"{size}","backup_id":"{label}"', end="")
except Exception:
    pass
PY
}

# write_status <leg> <true|false> [detail] - atomic per-leg freshness sentinel.
write_status() {
    # write_status <leg> <true|false> [detail] [extra-json]
    # extra-json is an optional JSON fragment of metric fields, e.g. '"size":"662 MiB","file_count":287'
    local leg="$1" ok="$2" detail="${3:-}" extra="${4:-}"
    # WRITE_STATUS_TS lets a caller stamp an explicit recovery-point time instead of "now" - the pg
    # WAL-freshness updater uses it so last_run_at reflects the last archived WAL, not the run time.
    local ts="${WRITE_STATUS_TS:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
    mkdir -p "$STATUS_DIR"
    local tmp
    tmp="$(mktemp "${STATUS_DIR}/.${leg}.XXXXXX")"
    printf '{"leg":"%s","ok":%s,"last_run_at":"%s","detail":"%s"%s}\n' \
        "$leg" "$ok" "$ts" "$detail" "${extra:+,$extra}" >"$tmp"
    mv -f "$tmp" "${STATUS_DIR}/${leg}.json"
    # Non-secret status; must be readable by the backend (a different uid in compose). (M12 #377)
    chmod 0644 "${STATUS_DIR}/${leg}.json"
}
