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
DATABASE_URL="${DOKTOK_DATABASE_URL:-postgresql://doktok:doktok@localhost:5433/doktok}"  # pragma: allowlist secret

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
    # Pass the program via -c (not a heredoc) so stdin stays bound to the piped JSON, not the script.
    python3 -c '
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
    print(f"\"size\":\"{size}\",\"backup_id\":\"{label}\"", end="")
except Exception:
    pass
' || true
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

# _sha256 <string> - sha256 hex of the given string (no trailing filename), portable across
# coreutils (sha256sum) and BSD/macOS (shasum). Empty output if neither is available.
_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s' "$1" | sha256sum | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1
    else
        printf ''
    fi
}

# _json_escape <string> - escape a string for safe embedding in a JSON string value. Handles
# backslash, double-quote, tab, newline, carriage-return. This is the ONLY way free text enters the
# append-only history - write_status's raw printf is NOT reused, so tampered/quoted detail strings
# can never break the one-line-per-event JSONL invariant or inject extra fields.
_json_escape() {
    local s="$1"
    s="${s//\\/\\\\}" # backslash first
    s="${s//\"/\\\"}" # double quote
    s="${s//$'\t'/\\t}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\n'/\\n}"
    printf '%s' "$s"
}

# History rotation threshold (lines). When history.jsonl grows past this it is rolled to .1 and a
# fresh file is started whose first line chains off the last archived line (hash chain continues).
HISTORY_MAX_LINES="${DOKTOK_HISTORY_MAX_LINES:-5000}"

# _history_append <line> - the serialized read-last + append step. flock-guarded when available so
# concurrent legs can't interleave and corrupt the seq/prev_sha256 chain; otherwise a single
# O_APPEND printf (atomic for short lines on a local fs) is the best-effort fallback.
_history_append() {
    local file="$1" line="$2"
    printf '%s\n' "$line" >>"$file"
    chmod 0644 "$file" 2>/dev/null || true
}

# log_event <leg> <event> <ok:true|false> [detail] [extra-json] - append ONE JSON line to the
# append-only, tamper-evident history (M12 DRP hardening). Outside Postgres (same dir/perms as the
# sentinels) so a DB restore can't roll history back. Fields are whitelisted; only the detail is free
# text (escaped + truncated). NEVER pass a secret, command line, raw stderr, filename, or tenant /
# document content as detail - the history is operator-visible and shipped offsite.
#   extra-json: a PRE-ESCAPED JSON metric fragment, e.g.
#     '"size":"662 MiB","item_count":287,"backup_id":"abc","duration_ms":48213'
log_event() {
    local leg="$1" event="$2" ok="${3:-false}" detail="${4:-}" extra="${5:-}"
    local ts="${WRITE_STATUS_TS:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
    mkdir -p "$STATUS_DIR"
    local file="${STATUS_DIR}/history.jsonl"
    local lock="${STATUS_DIR}/.history.lock"

    # Truncate detail to ~200 chars BEFORE escaping (so the limit is on the human text, not escapes),
    # then JSON-escape it. extra is trusted (callers build it from known-safe metric values).
    detail="${detail:0:200}"
    local esc_detail
    esc_detail="$(_json_escape "$detail")"

    # Serialize read-last + compute-chain + append. flock guards against interleaving legs.
    _emit() {
        local last_line="" prev_sha="" seq=1
        # Rotate when the file is too long; chain the new file off the last archived line.
        if [ -f "$file" ]; then
            local nlines
            nlines="$(wc -l <"$file" | tr -d ' ')"
            if [ "${nlines:-0}" -ge "$HISTORY_MAX_LINES" ]; then
                mv -f "$file" "${file}.1"
                last_line="$(tail -n 1 "${file}.1" 2>/dev/null || true)"
                prev_sha="$(_sha256 "$last_line")"
                # seq continues from the archived last line so the monotonic counter never resets.
                seq="$(printf '%s' "$last_line" | sed -n 's/.*"seq":\([0-9]\{1,\}\).*/\1/p')"
                seq="$((${seq:-0} + 1))"
                last_line=""
            fi
        fi
        if [ -f "$file" ] && [ -s "$file" ]; then
            last_line="$(tail -n 1 "$file" 2>/dev/null || true)"
            prev_sha="$(_sha256 "$last_line")"
            seq="$(printf '%s' "$last_line" | sed -n 's/.*"seq":\([0-9]\{1,\}\).*/\1/p')"
            seq="$((${seq:-0} + 1))"
        fi
        local line
        line="$(printf '{"schema":1,"seq":%d,"prev_sha256":"%s","ts":"%s","leg":"%s","event":"%s","ok":%s,"detail":"%s"%s}' \
            "$seq" "$prev_sha" "$ts" "$leg" "$event" "$ok" "$esc_detail" "${extra:+,$extra}")"
        _history_append "$file" "$line"
    }

    if command -v flock >/dev/null 2>&1; then
        (
            flock 9
            _emit
        ) 9>"$lock"
    else
        _emit
    fi
}
