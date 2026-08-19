#!/usr/bin/env bash
#
# Provision the Azure Blob offsite target for backups (M12 DEVOPS-B2, instance-aware #348,
# restic transport #827).
# Creates the resource group + storage account + container and turns on the controls that make
# offsite copies disaster/ransomware resistant: blob versioning, 30-day soft-delete (the safety
# net), and a lifecycle policy that expires ONLY the legacy tarball prefixes (pg-repo-/files-repo-
# - never the restic repo prefixes files//pg/, where a lifecycle delete would corrupt the repos;
# restic retention is forget/prune). Time-based container WORM was dropped in #827: it blocks the
# lock/index deletes restic prune needs (2026-08-18 incident). Ransomware delete-resistance comes
# from the two-SAS split instead: the hourly sync SAS is rwcl (NO delete), the delete-capable
# prune SAS stays host-only. The script finishes by running restic init for both Azure repos
# (azure:<container>:/files + :/pg) and printing the two-SAS setup guidance.
# Review-grade: run once per instance, with your Azure subscription (needs `az login` + restic);
# idempotent where the CLI allows.
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
#      DOKTOK_AZURE_CONTAINER_LTS (default doktok-backups-lts; only read to remove old
#      immutability policies), DOKTOK_AZURE_LOCATION (default westeurope),
#      DOKTOK_AZURE_COOL_AFTER_DAYS (default 30), DOKTOK_AZURE_DELETE_AFTER_DAYS (default 90),
#      DOKTOK_RESTIC_PASSWORD (restic repo encryption key; required for the repo init).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh
require az
require restic  # the Azure repo init at the end runs host-side

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
# #827: soft-delete (30d) is the safety net now; time-based WORM conflicts with restic prune.
az storage account blob-service-properties update -n "$ACCOUNT" \
    --enable-delete-retention true --delete-retention-days 30 >/dev/null
echo "removing time-based immutability policies if present (unlocked policies only)"
for c in "$CONTAINER" "${DOKTOK_AZURE_CONTAINER_LTS:-doktok-backups-lts}"; do
    az storage container immutability-policy delete --account-name "$ACCOUNT" -c "$c" \
        >/dev/null 2>&1 || warn "no (removable) immutability policy on $c - fine"
done

echo "container (no time-based WORM - soft-delete carries the protection)"
az storage container create --account-name "$ACCOUNT" -n "$CONTAINER" \
    --auth-mode login >/dev/null

echo "lifecycle policy: expire legacy tarballs ONLY (prefixes pg-repo-/files-repo-) - Cool after ${cool_after}d, delete after ${delete_after}d (never Archive); the restic repos (files/, pg/) manage their own retention"
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
        "filters": { "blobTypes": ["blockBlob"], "prefixMatch": ["pg-repo-", "files-repo-"] }
      }
    }
  ]
}
JSON
az storage account management-policy create --account-name "$ACCOUNT" -g "$RG" \
    --policy @"$policy_file" >/dev/null
rm -f "$policy_file"
trap 'err "azure provisioning FAILED"; exit 1' ERR

echo "restic init: the two Azure restic repos, azure:${CONTAINER}:/files + :/pg (idempotent)"
export AZURE_ACCOUNT_NAME="$ACCOUNT" RESTIC_PASSWORD="${DOKTOK_RESTIC_PASSWORD:?set DOKTOK_RESTIC_PASSWORD}"
# No SAS exists yet at provisioning time, and restic's Azure backend cannot ride the `az login`
# session (restic 0.14, spike 2026-08-18) - so init authenticates with the account key, read via
# the CLI session into this process's env only (never persisted). Unset any stray SAS env so the
# key auth wins.
unset AZURE_ACCOUNT_SAS 2>/dev/null || true
AZURE_ACCOUNT_KEY="$(az storage account keys list --account-name "$ACCOUNT" -g "$RG" \
    --query '[0].value' -o tsv)"
export AZURE_ACCOUNT_KEY
for prefix in files pg; do
    if restic -r "azure:${CONTAINER}:/${prefix}" cat config >/dev/null 2>&1; then
        ok "repo already exists: azure:${CONTAINER}:/${prefix}"
    else
        restic -r "azure:${CONTAINER}:/${prefix}" init >/dev/null
        ok "repo ready: azure:${CONTAINER}:/${prefix}"
    fi
done
unset AZURE_ACCOUNT_KEY AZURE_ACCOUNT_NAME RESTIC_PASSWORD

ok "Azure offsite ready: $RG / $ACCOUNT / $CONTAINER (restic repos: /files + /pg)"
ok "  versioning + 30d soft-delete + legacy-tarball lifecycle (Cool@${cool_after}d, delete@${delete_after}d)"
cat <<'EOF'
next: create TWO expiring, HTTPS-only SAS tokens on the backup account and store them off-box:
  DOKTOK_AZURE_SAS        --permissions rwcl  (NO delete)  - the hourly sync credential
  DOKTOK_AZURE_SAS_PRUNE  --permissions rwcld (delete)     - host-only, forget/prune step only
EOF
