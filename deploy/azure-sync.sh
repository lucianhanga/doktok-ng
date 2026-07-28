#!/usr/bin/env bash
#
# Offsite leg v3 (#766): push an ARCHIVED copy of the local backup repos to Azure Blob with a
# GFS rotation, tier-at-write, container-level WORM, and content dedup.
#
# Naming: <leg>-repo-<class>-<yyyymmdd-hhmmss>-<fp12>.tar.gz
#   class  hourly|daily|weekly|monthly|yearly (the GFS set the blob belongs to)
#   fp12   content fingerprint (files: latest restic snapshot id; pg: latest pgBackRest label +
#          WAL max). A leg whose newest offsite fingerprint matches the local one is NOT
#          re-uploaded; class promotions across a period boundary are server-side COPIES of the
#          newest blob, so long-lived classes stay populated without data transfer.
#
# Two containers (Azure enables version-level WORM only at account creation, so WORM is
# container-level and class-scoped instead):
#   short (DOKTOK_AZURE_CONTAINER,      default doktok-backups)      hourly+daily,  WORM ~2 days
#   lts   (DOKTOK_AZURE_CONTAINER_LTS,  default doktok-backups-lts)  weekly+,       WORM ~30 days
# Keep counts (GFS): 24 hourly / 7 daily / 4 weekly / 11 monthly / 1 yearly - pruned in code
# after each run (lifecycle rules cannot count; they only do tier transitions + the 2y safety
# net). Tier at write avoids early-deletion charges: hourly/daily Hot, weekly/monthly Cool
# (ladder to Cold/Archive via lifecycle), yearly Archive direct.
#
# Env: DOKTOK_AZURE_ACCOUNT, DOKTOK_AZURE_CONTAINER, DOKTOK_AZURE_CONTAINER_LTS; auth via
#      DOKTOK_AZURE_SAS (account-level, rwcl + DELETE - the prune needs it) / AZURE_STORAGE_KEY /
#      `az login`. DOKTOK_GFS_{HOURLY,DAILY,WEEKLY,MONTHLY,YEARLY}, DOKTOK_OFFSITE_MIN_SETS.
#      Pass --dry-run to plan without uploading.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

require az
require tar
: "${DOKTOK_AZURE_ACCOUNT:?set DOKTOK_AZURE_ACCOUNT}"
: "${DOKTOK_AZURE_CONTAINER:?set DOKTOK_AZURE_CONTAINER (short-lived sets)}"
CONTAINER_SHORT="$DOKTOK_AZURE_CONTAINER"
CONTAINER_LTS="${DOKTOK_AZURE_CONTAINER_LTS:-doktok-backups-lts}"
min_sets="${DOKTOK_OFFSITE_MIN_SETS:-3}"
dry_run="${1:-}"

# GFS keep counts + per-class tier (bash 3.2-safe case functions, no assoc arrays).
keep_for() {
    case "$1" in
        hourly) printf '%s' "${DOKTOK_GFS_HOURLY:-24}" ;;
        daily) printf '%s' "${DOKTOK_GFS_DAILY:-7}" ;;
        weekly) printf '%s' "${DOKTOK_GFS_WEEKLY:-4}" ;;
        monthly) printf '%s' "${DOKTOK_GFS_MONTHLY:-11}" ;;
        yearly) printf '%s' "${DOKTOK_GFS_YEARLY:-1}" ;;
    esac
}
tier_for() {
    case "$1" in
        hourly | daily) printf 'Hot' ;;
        weekly | monthly) printf 'Cool' ;;
        yearly) printf 'Archive' ;;
    esac
}
container_for() {
    case "$1" in
        hourly | daily) printf '%s' "$CONTAINER_SHORT" ;;
        *) printf '%s' "$CONTAINER_LTS" ;;
    esac
}

auth=()
[ -n "${DOKTOK_AZURE_SAS:-}" ] && auth+=(--sas-token "$DOKTOK_AZURE_SAS")

COMPOSE_FILES="${DOKTOK_COMPOSE_FILES:-docker-compose.prod.yml}"
COMPOSE_ENV_FILE="${DOKTOK_COMPOSE_ENV_FILE:-.env.production}"
compose=(docker compose)
for f in ${COMPOSE_FILES//,/ }; do
    compose+=(-f "$f")
done
compose+=(--env-file "$COMPOSE_ENV_FILE")

fail_sync() {
    local msg="$1"
    write_status offsite false "azure sync failed: ${msg}"
    err "azure sync FAILED: ${msg}"
    exit 1
}
trap 'fail_sync "unexpected error"' ERR

# --- helpers ---------------------------------------------------------------------

_fp12() { _sha256 "$1" | cut -c1-12; }

# sha256 hex of stdin (portable; mirrors lib.sh's _sha256 for strings).
_sha256_pipe() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum | cut -d' ' -f1
    else
        shasum -a 256 | cut -d' ' -f1
    fi
}

# Latest content fingerprint per leg (empty on failure - the leg then uploads to be safe).
current_fp() {
    case "$1" in
        files)
            # Host-side fingerprint of the files_root: sorted path+size list. The pipeline is
            # write-once (never edits in place), so this changes exactly when content does. The
            # restic tree id is useless here - it embeds directory mtimes, which the 15-min
            # staging copy refreshes even with zero real changes.
            { find "$FILES_ROOT" -type f ! -name '.DS_Store' -exec stat -f '%N %z' {} + 2>/dev/null \
                || find "$FILES_ROOT" -type f ! -name '.DS_Store' -exec stat -c '%n %s' {} +; } \
                | sort | _sha256_pipe
            ;;
        pg)
            "${compose[@]}" exec -u postgres -T db pgbackrest --stanza=doktok info --output=json 2>/dev/null \
                | grep -oE '"label":"[^"]*"|"max":"[0-9A-F/]+"' | tail -2 | tr '\n' '|' || true
            ;;
    esac
}

# All blob names for <container> <prefix>, newline-separated, sorted.
list_blobs() { # <container> <prefix>
    az storage blob list --account-name "$DOKTOK_AZURE_ACCOUNT" \
        --container-name "$1" --prefix "$2" \
        --query "[].name" -o tsv "${auth[@]}" 2>/dev/null | sort || true
}

# The temporally newest blob from a mixed-class listing: the CLASS prefix scrambles lexical name
# order (daily < hourly < ... < yearly), so sort by the timestamp embedded in the name instead.
newest_blob() {
    sed -E 's/.*-repo-[a-z]+-([0-9]{8}-[0-9]{6})-.*/\1 &/' | sort | tail -1 | cut -d' ' -f2
}

# name_parts <blobname> -> "<class> <yyyymmdd-hhmmss> <fp12>"
name_parts() {
    local n="${1%.tar.gz}"      # <leg>-repo-<class>-<ts>-<fp>
    n="${n#*-repo-}"
    local cls="${n%%-*}"; n="${n#*-}"
    local ts="${n%%-*}"; n="${n#*-}"
    local ts2="${n%%-*}"; n="${n#*-}"  # hhmmss half of the ts
    printf '%s %s-%s %s' "$cls" "$ts" "$ts2" "$n"
}

# period key for a ts (yyyymmdd-hhmmss): day=yyyymmdd, week=weeks-since-epoch, month=yyyymm, year=yyyy
_period_key() { # <class> <yyyymmdd>
    local d="$2"
    case "$1" in
        daily) printf '%s' "$d" ;;
        weekly)
            local epoch
            epoch="$(date -u -d "${d:0:4}-${d:4:2}-${d:6:2}" +%s 2>/dev/null \
                || date -u -j -f '%Y%m%d' "$d" +%s 2>/dev/null || echo 0)"
            printf '%s' "$(( (epoch + 259200) / 604800 ))"
            ;;
        monthly) printf '%s' "${d:0:6}" ;;
        yearly) printf '%s' "${d:0:4}" ;;
    esac
}

# Upload one blob into the class's container, then tier it.
_put_blob() { # <file> <blobname> <class>
    local file="$1" name="$2" cls="$3"
    az storage blob upload --account-name "$DOKTOK_AZURE_ACCOUNT" \
        --container-name "$(container_for "$cls")" --name "$name" --file "$file" \
        --overwrite false "${auth[@]}" >/dev/null
    az storage blob set-tier --account-name "$DOKTOK_AZURE_ACCOUNT" \
        --container-name "$(container_for "$cls")" --name "$name" \
        --tier "$(tier_for "$cls")" "${auth[@]}" >/dev/null 2>&1 || true
}

# Copy server-side (cross-container, async - synchronous copies are capped at 256MB and pg
# tarballs exceed that); poll briefly for completion - promotions are not latency-critical, so a
# still-pending copy only warns. Then tier it.
_promote() { # <src-name> <dst-name> <class>
    local src="$1" dst="$2" cls="$3"
    local src_container dst_container
    src_container="$(container_for hourly)"
    dst_container="$(container_for "$cls")"
    az storage blob copy start --account-name "$DOKTOK_AZURE_ACCOUNT" \
        --source-container "$src_container" --source-blob "$src" \
        --destination-container "$dst_container" --destination-blob "$dst" \
        "${auth[@]}" >/dev/null
    local i status=""
    for i in $(seq 1 15); do
        status="$(az storage blob show --account-name "$DOKTOK_AZURE_ACCOUNT" \
            --container-name "$dst_container" --name "$dst" \
            --query "properties.copy.status" -o tsv "${auth[@]}" 2>/dev/null || true)"
        [ "$status" = "success" ] && break
        sleep 2
    done
    [ "$status" = "success" ] || warn "copy to $cls still pending ($status) - will settle in background"
    az storage blob set-tier --account-name "$DOKTOK_AZURE_ACCOUNT" \
        --container-name "$dst_container" --name "$dst" \
        --tier "$(tier_for "$cls")" "${auth[@]}" >/dev/null 2>&1 || true
}

# Prune a class to its keep count (in its container); WORM-window deletes fail loudly but not
# fatally - they are retried by later runs once the container policy window lapses.
_prune() { # <leg> <class>
    local keep names container count drop
    keep="$(keep_for "$2")"
    container="$(container_for "$2")"
    names="$(list_blobs "$container" "$1-repo-$2-")"
    count="$(printf '%s\n' "$names" | grep -c . || true)"
    [ "$count" -gt "$keep" ] || return 0
    drop=$((count - keep))
    warn "gfs prune: $1/$2 has $count sets (keep $keep) - deleting oldest $drop"
    printf '%s\n' "$names" | head -"$drop" | while read -r name; do
        [ -n "$name" ] || continue
        az storage blob delete --account-name "$DOKTOK_AZURE_ACCOUNT" \
            --container-name "$container" --name "$name" "${auth[@]}" >/dev/null 2>&1 \
            || warn "  (kept for now, still in WORM window: $name)"
    done
}

# --- main ------------------------------------------------------------------------

[ -d "$BACKUP_DIR/pg" ] || fail_sync "no pg repo at $BACKUP_DIR/pg - run a backup first"
[ -d "$FILES_REPO" ] || fail_sync "no files repo at $FILES_REPO - run a backup first"

ts="$(date -u +%Y%m%d-%H%M%S)"
day="${ts%%-*}"
staging="$(mktemp -d)"
trap 'rm -rf "$staging"; fail_sync "unexpected error"' ERR

fp_pg="" fp_files=""

for leg in pg files; do
    fp_raw="$(current_fp "$leg")"
    [ -n "$fp_raw" ] || fail_sync "cannot fingerprint the $leg repo"
    fp="$(_fp12 "$fp_raw")"

    all="$(list_blobs "$CONTAINER_SHORT" "${leg}-repo-")
$(list_blobs "$CONTAINER_LTS" "${leg}-repo-")"
    newest="$(printf '%s\n' "$all" | newest_blob)"
    off_fp=""
    [ -n "$newest" ] && off_fp="$(name_parts "$newest" | awk '{print $3}')"

    if [ "$off_fp" = "$fp" ]; then
        echo "$leg: content unchanged (fp=$fp) - skipping upload"
    else
        [ "$dry_run" = "--dry-run" ] && {
            warn "$leg: dry-run - WOULD bundle + upload ${leg}-repo-hourly-${ts}-${fp}.tar.gz"
        } || {
            [ -d "$BACKUP_DIR/$leg" ] || fail_sync "no $leg repo to bundle"
            echo "$leg: bundling + uploading (fp=$fp, was ${off_fp:-none})"
            tar -czf "$staging/${leg}-repo-${ts}.tar.gz" -C "$BACKUP_DIR" \
                --exclude='pg/log' --exclude='pg/lock' "$leg"
            _put_blob "$staging/${leg}-repo-${ts}.tar.gz" \
                "${leg}-repo-hourly-${ts}-${fp}.tar.gz" hourly
            newest="${leg}-repo-hourly-${ts}-${fp}.tar.gz"
        }
    fi
    if [ "$leg" = "pg" ]; then fp_pg="$fp"; else fp_files="$fp"; fi

    # Promotions across period boundaries (server-side copies of the newest blob).
    [ "$dry_run" = "--dry-run" ] && continue
    [ -n "$newest" ] || continue
    for cls in daily weekly monthly yearly; do
        cls_container="$(container_for "$cls")"
        newest_in_class="$(list_blobs "$cls_container" "${leg}-repo-${cls}-" | tail -1)"
        have_key="none"
        [ -n "$newest_in_class" ] && have_key="$(_period_key "$cls" "$(name_parts "$newest_in_class" | awk '{print $2}' | cut -d- -f1)")"
        want_key="$(_period_key "$cls" "$day")"
        if [ "$have_key" != "$want_key" ]; then
            echo "$leg: promote -> ${cls} (${have_key} -> ${want_key})"
            _promote "$newest" "${leg}-repo-${cls}-${ts}-${fp}.tar.gz" "$cls"
        fi
    done
done

rm -rf "$staging"
trap 'fail_sync "unexpected error"' ERR

# GFS prune per leg+class.
[ "$dry_run" = "--dry-run" ] || {
    for leg in pg files; do
        for cls in hourly daily weekly monthly yearly; do
            _prune "$leg" "$cls"
        done
    done
}

# Audit: sets per class + fingerprint freshness (content-time, not upload-time).
pg_sets="$({ list_blobs "$CONTAINER_SHORT" "pg-repo-"; list_blobs "$CONTAINER_LTS" "pg-repo-"; } | grep -c . || true)"
files_sets="$({ list_blobs "$CONTAINER_SHORT" "files-repo-"; list_blobs "$CONTAINER_LTS" "files-repo-"; } | grep -c . || true)"
fp_state="pg=${fp_pg:-?} files=${fp_files:-?}"
detail="gfs sets pg=${pg_sets} files=${files_sets} fp[${fp_state}] (content current)"
if [ "$dry_run" != "--dry-run" ]; then
    if [ "${pg_sets:-0}" -lt "$min_sets" ] || [ "${files_sets:-0}" -lt "$min_sets" ]; then
        write_status offsite true "WARN: fewer than ${min_sets} offsite sets - ${detail}"
        warn "offsite history below the minimum (${min_sets}); it builds up over the next runs"
    else
        write_status offsite true "$detail"
    fi
    log_event offsite success true "$detail" "\"item_count\":${pg_sets}"
fi
ok "offsite sync complete (${detail})"
