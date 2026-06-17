#!/usr/bin/env bash
#
# Offsite leg (M12): push the local backup repository to Azure Blob. Local-first means everything is
# already staged in $DOKTOK_BACKUP_DIR (restic repo + pgBackRest repo); this only uploads it, so the
# whole backup/restore engine works without any cloud account and this step is a thin, swappable hop.
#
# Apply immutability (time-based retention or legal hold) + versioning ON THE CONTAINER in Azure -
# that is the ransomware/object-lock control and is configured account-side, not here.
#
# Env: DOKTOK_AZURE_ACCOUNT, DOKTOK_AZURE_CONTAINER (required); auth via DOKTOK_AZURE_SAS, or
#      AZURE_STORAGE_KEY, or `az login` (managed identity). Pass --dry-run to preview.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

require az
: "${DOKTOK_AZURE_ACCOUNT:?set DOKTOK_AZURE_ACCOUNT}"
: "${DOKTOK_AZURE_CONTAINER:?set DOKTOK_AZURE_CONTAINER}"
trap 'err "azure sync FAILED"; exit 1' ERR

[ -d "$BACKUP_DIR" ] || {
    err "no local backup repository at $BACKUP_DIR - run a backup first"
    exit 1
}

extra=()
[ -n "${DOKTOK_AZURE_SAS:-}" ] && extra+=(--sas-token "$DOKTOK_AZURE_SAS")
[ "${1:-}" = "--dry-run" ] && extra+=(--dryrun)

echo "syncing $BACKUP_DIR -> azure://${DOKTOK_AZURE_ACCOUNT}/${DOKTOK_AZURE_CONTAINER}"
az storage blob sync \
    --account-name "$DOKTOK_AZURE_ACCOUNT" \
    --container "$DOKTOK_AZURE_CONTAINER" \
    --source "$BACKUP_DIR" \
    "${extra[@]}"

ok "offsite sync complete (restic + pgBackRest repos are ciphertext, so Azure only sees encrypted data)"
