#!/usr/bin/env bash
#
# Provision the Azure Blob offsite target for backups (M12 DEVOPS-B2, instance-aware #348).
# Creates the resource group + storage account + container and turns on the controls that make
# offsite copies disaster/ransomware resistant: blob versioning, a time-based immutability
# (retention) policy, and a lifecycle policy that tiers old blobs to Cool and expires them past
# retention (recent backups stay in Hot/Cool - Archive rehydration takes hours and would blow RTO).
# Review-grade: run once per instance, with your Azure subscription (needs `az login`); idempotent
# where the CLI allows.
#
# Multi-instance naming: every doktok-ng instance backs up independently. When the names are not
# given explicitly they are derived from DOKTOK_INSTANCE_ID (12 hex chars, generated once and
# persisted in .env on first run):
#   resource group   doktok-<id>-rg
#   storage account  doktokbkp<id>     (Azure: lowercase+digits, 3-24 chars, globally unique)
#   container        doktok-backups
# Explicit DOKTOK_AZURE_RG / DOKTOK_AZURE_ACCOUNT / DOKTOK_AZURE_CONTAINER always win.
#
# Env: DOKTOK_INSTANCE_ID, DOKTOK_AZURE_RG, DOKTOK_AZURE_ACCOUNT, DOKTOK_AZURE_CONTAINER,
#      DOKTOK_AZURE_LOCATION (default westeurope), DOKTOK_AZURE_RETENTION_DAYS (immutability,
#      default 30), DOKTOK_AZURE_COOL_AFTER_DAYS (default 30), DOKTOK_AZURE_DELETE_AFTER_DAYS
#      (default 90).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh
require az

# Resolve the instance identity (generate + persist in .env on first use, when writable).
instance="${DOKTOK_INSTANCE_ID:-}"
if [ -z "$instance" ]; then
    instance="$(uuidgen 2>/dev/null | tr -d '[:upper:]-' | tr 'A-F' 'a-f' | cut -c1-12 || true)"
    [ -n "$instance" ] || instance="$(od -An -N6 -tx1 /dev/urandom | tr -d ' \n')"
    if [ -w .env ]; then
        printf '\n# Azure offsite backup identity (#348): one id per doktok-ng instance\nDOKTOK_INSTANCE_ID=%s\n' \
            "$instance" >>.env
        warn "generated DOKTOK_INSTANCE_ID=$instance and persisted it in .env (store it off-box too)"
    else
        warn "generated DOKTOK_INSTANCE_ID=$instance - persist it in your backup.env NOW"
    fi
fi

RG="${DOKTOK_AZURE_RG:-doktok-${instance}-rg}"
ACCOUNT="${DOKTOK_AZURE_ACCOUNT:-doktokbkp${instance}}"
CONTAINER="${DOKTOK_AZURE_CONTAINER:-doktok-backups}"
location="${DOKTOK_AZURE_LOCATION:-westeurope}"
retention="${DOKTOK_AZURE_RETENTION_DAYS:-30}"
cool_after="${DOKTOK_AZURE_COOL_AFTER_DAYS:-30}"
delete_after="${DOKTOK_AZURE_DELETE_AFTER_DAYS:-90}"
trap 'err "azure provisioning FAILED"; exit 1' ERR

[ "${#ACCOUNT}" -le 24 ] || { err "storage account name '$ACCOUNT' is > 24 chars (Azure limit)"; exit 1; }

warn "keep recent backups in Hot/Cool, NOT Archive - Archive rehydration is hours and would blow RTO"
echo "instance=$instance rg=$RG account=$ACCOUNT container=$CONTAINER location=$location"

echo "resource group + storage account (Standard_LRS, TLS1.2, versioning on, tagged)"
az group create -n "$RG" -l "$location" \
    --tags app=doktok-ng instance="$instance" purpose=backup >/dev/null
az storage account create -n "$ACCOUNT" -g "$RG" -l "$location" \
    --sku Standard_LRS --min-tls-version TLS1_2 --allow-blob-public-access false \
    --tags app=doktok-ng instance="$instance" purpose=backup >/dev/null
az storage account blob-service-properties update -n "$ACCOUNT" \
    --enable-versioning true >/dev/null

echo "container with version-level immutability support"
az storage container create --account-name "$ACCOUNT" -n "$CONTAINER" \
    --auth-mode login >/dev/null
echo "time-based immutability policy: ${retention} days (locked policies cannot be shortened)"
az storage container immutability-policy create --account-name "$ACCOUNT" \
    -c "$CONTAINER" --period "$retention" --allow-protected-append-writes true >/dev/null || \
    warn "immutability policy may already exist; review it in the portal"

echo "lifecycle policy: Cool after ${cool_after}d, delete after ${delete_after}d (never Archive)"
policy_file="$(mktemp)"
trap 'rm -f "$policy_file"; err "azure provisioning FAILED"; exit 1' ERR
cat >"$policy_file" <<JSON
{
  "rules": [
    {
      "enabled": true,
      "name": "tier-and-expire",
      "type": "Lifecycle",
      "definition": {
        "actions": {
          "baseBlob": {
            "tierToCool": { "daysAfterModificationGreaterThan": ${cool_after} },
            "delete": { "daysAfterModificationGreaterThan": ${delete_after} }
          }
        },
        "filters": { "blobTypes": ["blockBlob"], "prefixMatch": [] }
      }
    }
  ]
}
JSON
az storage account management-policy create --account-name "$ACCOUNT" -g "$RG" \
    --policy @"$policy_file" >/dev/null
rm -f "$policy_file"
trap 'err "azure provisioning FAILED"; exit 1' ERR

ok "Azure offsite ready: $RG / $ACCOUNT / $CONTAINER"
ok "  versioning + ${retention}d immutability + lifecycle (Cool@${cool_after}d, delete@${delete_after}d)"
warn "next: create a write-scoped SAS (write+create+list, NO delete, HTTPS-only, expiring) as DOKTOK_AZURE_SAS and store it off-box"
