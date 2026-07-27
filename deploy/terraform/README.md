# Offsite backup infrastructure (Terraform)

One deployment per doktok-ng instance, deriving all names from `instance_id` (12 hex chars,
persisted in the instance's `.env` as `DOKTOK_INSTANCE_ID`): RG `doktok-<id>-rg`, storage account
`doktokbkp<id>`, container `doktok-backups`.

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
terraform import azurerm_storage_container_immutability_policy.backup \
  "/subscriptions/$SUB/resourceGroups/doktok-<id>-rg/providers/Microsoft.Storage/storageAccounts/doktokbkp<id>/blobServices/default/containers/doktok-backups/immutabilityPolicies/default"
terraform import azurerm_storage_management_policy.backup \
  "/subscriptions/$SUB/resourceGroups/doktok-<id>-rg/providers/Microsoft.Storage/storageAccounts/doktokbkp<id>/managementPolicies/default"
```

Then `terraform plan` should show at most the lifecycle-ladder update. **Terraform and
`azure-provision.sh` must not both manage the same account afterwards — Terraform wins.**

## What it manages

- Storage account: Standard_LRS, TLS1.2, no public access, blob versioning, tags.
- Immutability policy: `retention_days` (default 30), protected append writes, unlocked (lock it
  deliberately — a locked policy can never be shortened).
- Lifecycle ladder: Cool at 30d, Cold at 90d, Archive at 180d (offline — restores from that depth
  take hours), delete at 365d. All tunable via variables. The minimum-COUNT floor cannot be a
  lifecycle rule; `deploy/azure-sync.sh` enforces it operationally after each upload.

## Local state note

`terraform.tfvars` holds the instance's `instance_id` for this checkout (gitignored, not secret).
