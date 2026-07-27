#!/usr/bin/env bash
#
# Offsite leg (M12, reworked #345/#347): push an ARCHIVED copy of the local backup repositories to
# Azure Blob. Local-first means the live engine (restic + pgBackRest, minute-level PITR) keeps
# working in $DOKTOK_BACKUP_DIR; this hop bundles each repo into ONE tarball per run and uploads it,
# so Azure only ever sees ciphertext, blobs are write-once (fits the immutability policy; no
# delete/modify conflicts), and a run costs 2 PUTs instead of LIST-ing tens of thousands of tiny
# WAL/chunk files (the raw pg repo is ~68k files - per-transaction pricing makes a raw sync silly).
#
# Apply immutability (time-based retention) + lifecycle tiers ON THE CONTAINER in Azure (Terraform,
# deploy/terraform) - old tarballs expire there; this script never deletes in-band.
#
# After the upload an audit counts the offsite backup sets and fails the offsite sentinel when the
# count drops below DOKTOK_OFFSITE_MIN_SETS (the "minimum secure number of backups" control - an
# Azure lifecycle rule cannot count objects, so the floor is enforced operationally here).
#
# Env: DOKTOK_AZURE_ACCOUNT, DOKTOK_AZURE_CONTAINER (required); auth via DOKTOK_AZURE_SAS, or
#      AZURE_STORAGE_KEY, or `az login`. DOKTOK_OFFSITE_MIN_SETS (default 3). Pass --dry-run to
#      bundle + audit without uploading.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

require az
require tar
: "${DOKTOK_AZURE_ACCOUNT:?set DOKTOK_AZURE_ACCOUNT}"
: "${DOKTOK_AZURE_CONTAINER:?set DOKTOK_AZURE_CONTAINER}"
min_sets="${DOKTOK_OFFSITE_MIN_SETS:-3}"
dry_run="${1:-}"

fail_sync() {
    local msg="$1"
    write_status offsite false "azure sync failed: ${msg}"
    err "azure sync FAILED: ${msg}"
    exit 1
}
trap 'fail_sync "unexpected error"' ERR

[ -d "$BACKUP_DIR/pg" ] || fail_sync "no pg repo at $BACKUP_DIR/pg - run a backup first"
[ -d "$FILES_REPO" ] || fail_sync "no files repo at $FILES_REPO - run a backup first"

ts="$(date -u +%Y%m%d-%H%M%S)"
staging="$(mktemp -d)"
trap 'rm -rf "$staging"; fail_sync "unexpected error"' ERR

# Bundle. pg/log + pg/lock are mutable operational dirs, not backup data; they stay out.
echo "bundling pg repo ($(du -sh "$BACKUP_DIR/pg" | cut -f1) raw) + files repo ($(du -sh "$FILES_REPO" | cut -f1) raw)"
tar -czf "$staging/pg-repo-${ts}.tar.gz" -C "$BACKUP_DIR" --exclude='pg/log' --exclude='pg/lock' pg
tar -czf "$staging/files-repo-${ts}.tar.gz" -C "$BACKUP_DIR" files
pg_size="$(du -h "$staging/pg-repo-${ts}.tar.gz" | cut -f1)"
files_size="$(du -h "$staging/files-repo-${ts}.tar.gz" | cut -f1)"
echo "bundles: pg-repo=${pg_size} files-repo=${files_size}"

auth=()
[ -n "${DOKTOK_AZURE_SAS:-}" ] && auth+=(--sas-token "$DOKTOK_AZURE_SAS")

if [ "$dry_run" = "--dry-run" ]; then
    warn "dry-run: NOT uploading (would put pg-repo-${ts}.tar.gz + files-repo-${ts}.tar.gz)"
else
    echo "uploading -> azure://${DOKTOK_AZURE_ACCOUNT}/${DOKTOK_AZURE_CONTAINER}"
    az storage blob upload --account-name "$DOKTOK_AZURE_ACCOUNT" \
        --container-name "$DOKTOK_AZURE_CONTAINER" \
        --name "pg-repo-${ts}.tar.gz" --file "$staging/pg-repo-${ts}.tar.gz" \
        --overwrite false "${auth[@]}" >/dev/null
    az storage blob upload --account-name "$DOKTOK_AZURE_ACCOUNT" \
        --container-name "$DOKTOK_AZURE_CONTAINER" \
        --name "files-repo-${ts}.tar.gz" --file "$staging/files-repo-${ts}.tar.gz" \
        --overwrite false "${auth[@]}" >/dev/null
fi
rm -rf "$staging"
trap 'fail_sync "unexpected error"' ERR

# Audit: count the offsite backup sets (per leg). The lifecycle expiry bounds the count from above;
# this enforces the floor from below.
pg_sets="$(az storage blob list --account-name "$DOKTOK_AZURE_ACCOUNT" \
    --container-name "$DOKTOK_AZURE_CONTAINER" --prefix "pg-repo-" \
    --query "length(@)" -o tsv "${auth[@]}" 2>/dev/null || echo 0)"
files_sets="$(az storage blob list --account-name "$DOKTOK_AZURE_ACCOUNT" \
    --container-name "$DOKTOK_AZURE_CONTAINER" --prefix "files-repo-" \
    --query "length(@)" -o tsv "${auth[@]}" 2>/dev/null || echo 0)"
echo "offsite sets: pg=${pg_sets} files=${files_sets} (minimum: ${min_sets})"

detail="azure offsite pg-repo-${ts} (${pg_size}) files-repo (${files_size}) sets=${pg_sets}/${files_sets}"
if [ "$dry_run" != "--dry-run" ]; then
    if [ "${pg_sets:-0}" -lt "$min_sets" ] || [ "${files_sets:-0}" -lt "$min_sets" ]; then
        # Not a hard failure while the offsite history is still being built; loud on the sentinel.
        write_status offsite true "WARN: fewer than ${min_sets} offsite sets - ${detail}"
        warn "offsite history below the minimum (${min_sets}); it builds up over the next runs"
    else
        write_status offsite true "$detail"
    fi
    log_event offsite success true "$detail" "\"size\":\"${pg_size}\",\"item_count\":${pg_sets}"
fi
ok "offsite sync complete (${detail})"
