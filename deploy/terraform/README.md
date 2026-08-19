# Offsite backup infrastructure (Terraform)

One deployment per doktok-ng instance, deriving all names from `instance_id` (12 hex chars,
persisted in the instance's `.env` as `DOKTOK_INSTANCE_ID`): RG `doktok-<id>-rg`, storage account
`doktokbkp<id>`, containers `doktok-backups` + `doktok-backups-lts` (the lts split is a legacy of
the #766 tarball design; the restic repos live at the `files/` + `pg/` prefixes of
`doktok-backups`).

## First use on a new instance

```bash
terraform init
terraform apply -var="instance_id=<12 hex>"
```

Auth: the azurerm provider uses your `az login` session. State is local by default (gitignored);
move to a remote backend before more than one operator manages the same instance.

## Adopting an instance provisioned by deploy/azure-provision.sh

Import the existing resources (subscription id from `az account show`):

```bash
SUB=<subscription-id>
terraform import azurerm_resource_group.backup            "/subscriptions/$SUB/resourceGroups/doktok-<id>-rg"
terraform import azurerm_storage_account.backup           "/subscriptions/$SUB/resourceGroups/doktok-<id>-rg/providers/Microsoft.Storage/storageAccounts/doktokbkp<id>"
terraform import azurerm_storage_container.backup         "/subscriptions/$SUB/resourceGroups/doktok-<id>-rg/providers/Microsoft.Storage/storageAccounts/doktokbkp<id>/blobServices/default/containers/doktok-backups"
terraform import azurerm_storage_container.backup_lts     "/subscriptions/$SUB/resourceGroups/doktok-<id>-rg/providers/Microsoft.Storage/storageAccounts/doktokbkp<id>/blobServices/default/containers/doktok-backups-lts"
terraform import azurerm_storage_management_policy.backup \
  "/subscriptions/$SUB/resourceGroups/doktok-<id>-rg/providers/Microsoft.Storage/storageAccounts/doktokbkp<id>/managementPolicies/default"
```

Then `terraform plan` should show at most the lifecycle-ladder update. **Terraform and
`azure-provision.sh` must not both manage the same account afterwards — Terraform wins.**

## What it manages

- Storage account: Standard_LRS, TLS1.2, no public access, blob versioning + 30-day soft-delete
  (the ransomware safety net), tags.
- Both containers WITHOUT time-based immutability (WORM dropped in #827 - it blocks the deletes
  restic prune needs; delete-resistance is the soft-delete + two-SAS split, see ADR-0026).
- Lifecycle rule scoped to the legacy tarball prefixes (`pg-repo-`/`files-repo-`) ONLY: Cool at
  30d, Cold at 90d, Archive at 180d (offline — restores from that depth take hours), delete at
  730d as the safety net. All tunable via variables. It must never match the restic repo prefixes
  (`files/`, `pg/`) - a lifecycle delete inside a restic repo corrupts it; restic retention is
  `forget --prune` in `deploy/azure-sync.sh`, which also audits the offsite snapshot count
  (`DOKTOK_OFFSITE_MIN_SETS`).

## Local state note

`terraform.tfvars` holds the instance's `instance_id` for this checkout (gitignored, not secret).
