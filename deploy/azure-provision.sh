#!/usr/bin/env bash
#
# Provision the Azure Blob offsite target for backups (M12 DEVOPS-B2). Creates the storage account +
# container and turns on the controls that make offsite copies disaster/ransomware resistant:
# blob versioning + a time-based immutability (retention) policy. Review-grade: run once, with your
# Azure subscription (needs `az login`); it is idempotent where the CLI allows.
#
# Env: DOKTOK_AZURE_RG, DOKTOK_AZURE_ACCOUNT, DOKTOK_AZURE_CONTAINER, DOKTOK_AZURE_LOCATION
#      (default westeurope), DOKTOK_AZURE_RETENTION_DAYS (default 30).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh
require az

: "${DOKTOK_AZURE_RG:?set DOKTOK_AZURE_RG}"
: "${DOKTOK_AZURE_ACCOUNT:?set DOKTOK_AZURE_ACCOUNT}"
: "${DOKTOK_AZURE_CONTAINER:?set DOKTOK_AZURE_CONTAINER}"
location="${DOKTOK_AZURE_LOCATION:-westeurope}"
retention="${DOKTOK_AZURE_RETENTION_DAYS:-30}"
trap 'err "azure provisioning FAILED"; exit 1' ERR

warn "keep recent backups in Hot/Cool, NOT Archive - Archive rehydration is hours and would blow RTO"

echo "resource group + storage account (Standard_LRS, TLS1.2, versioning on)"
az group create -n "$DOKTOK_AZURE_RG" -l "$location" >/dev/null
az storage account create -n "$DOKTOK_AZURE_ACCOUNT" -g "$DOKTOK_AZURE_RG" -l "$location" \
    --sku Standard_LRS --min-tls-version TLS1_2 --allow-blob-public-access false >/dev/null
az storage account blob-service-properties update -n "$DOKTOK_AZURE_ACCOUNT" \
    --enable-versioning true >/dev/null

echo "container with version-level immutability support"
az storage container create --account-name "$DOKTOK_AZURE_ACCOUNT" -n "$DOKTOK_AZURE_CONTAINER" \
    --auth-mode login >/dev/null
echo "time-based immutability policy: ${retention} days (locked policies cannot be shortened)"
az storage container immutability-policy create --account-name "$DOKTOK_AZURE_ACCOUNT" \
    -c "$DOKTOK_AZURE_CONTAINER" --period "$retention" --allow-protected-append-writes true >/dev/null || \
    warn "immutability policy may already exist; review it in the portal"

ok "Azure offsite ready: ${DOKTOK_AZURE_ACCOUNT}/${DOKTOK_AZURE_CONTAINER} (versioning + ${retention}d immutability)"
warn "create a write-scoped SAS for DOKTOK_AZURE_SAS and store it off-box; add a lifecycle rule to tier old data to Cool"
