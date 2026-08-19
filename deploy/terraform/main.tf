# DokTok NG offsite backup infrastructure (Azure Blob) as code (#348/#345/#347, restic #827).
#
# One deployment per doktok-ng instance. All names derive from var.instance_id (12 hex chars,
# persisted in the instance's .env as DOKTOK_INSTANCE_ID) so independent instances never collide:
#   resource group   doktok-<id>-rg
#   storage account  doktokbkp<id>     (Azure: lowercase+digits, 3-24 chars, globally unique)
#   container        doktok-backups
#
# Controls: blob versioning + 30-day soft-delete (the ransomware safety net) + a lifecycle rule
# scoped to the legacy tarball prefixes (pg-repo-/files-repo-) ONLY - it must never match the
# restic repos at files//pg/ (a lifecycle delete there corrupts the repos; restic retention is
# forget/prune). Time-based container WORM was dropped in #827: it blocks the lock/index deletes
# restic prune needs (2026-08-18 incident). Ransomware delete-resistance comes from the two-SAS
# split: the hourly sync SAS is rwcl (NO delete), the delete-capable prune SAS stays host-only.
#
# Usage (auth via `az login`; the azurerm provider picks up the CLI session):
#   terraform init
#   terraform plan  -var="instance_id=<12 hex>"
#   terraform apply -var="instance_id=<12 hex>"
# State is local by default (gitignored) - move to a remote backend before more than one operator
# manages this.

terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
}

variable "instance_id" {
  type        = string
  description = "12 hex chars, unique per doktok-ng instance (persisted in .env as DOKTOK_INSTANCE_ID)."
  validation {
    condition     = can(regex("^[0-9a-f]{12}$", var.instance_id))
    error_message = "instance_id must be exactly 12 lowercase hex chars."
  }
}

variable "location" {
  type        = string
  default     = "westeurope"
  description = "Azure region for the backup resources."
}

variable "cool_after_days" {
  type        = number
  default     = 30
  description = "Tier Hot -> Cool after this many days."
}

variable "cold_after_days" {
  type        = number
  default     = 90
  description = "Tier Cool -> Cold after this many days."
}

variable "archive_after_days" {
  type        = number
  default     = 180
  description = "Tier Cold -> Archive after this many days. Archive is OFFLINE (rehydration takes hours) - acceptable only this deep, protects RTO for everything newer."
}

variable "delete_after_days" {
  type        = number
  default     = 730
  description = "Safety-net expiry (days) for the legacy tarball prefixes. GFS retention for the restic repos is forget/prune inside deploy/azure-sync.sh (#827); the minimum offsite snapshot count is audited there too (DOKTOK_OFFSITE_MIN_SETS)."
}

locals {
  rg            = "doktok-${var.instance_id}-rg"
  account       = "doktokbkp${var.instance_id}"
  container     = "doktok-backups"
  container_lts = "doktok-backups-lts"
  tags = {
    app      = "doktok-ng"
    instance = var.instance_id
    purpose  = "backup"
  }
}

resource "azurerm_resource_group" "backup" {
  name     = local.rg
  location = var.location
  tags     = local.tags
}

resource "azurerm_storage_account" "backup" {
  name                            = local.account
  resource_group_name             = azurerm_resource_group.backup.name
  location                        = azurerm_resource_group.backup.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = local.tags

  blob_properties {
    versioning_enabled = true

    # #827: soft-delete (30d) is the ransomware safety net now that container WORM is gone
    # (WORM blocked the lock/index deletes restic prune needs - 2026-08-18 incident).
    delete_retention_policy {
      days = 30
    }
  }
}

# Two containers (the short/lts split is a legacy of the tarball GFS design, #766) WITHOUT
# time-based immutability (#827): WORM conflicts with restic prune, so soft-delete + the
# two-SAS split carry the ransomware protection. The restic repos live at the files//pg/
# prefixes of the short container.
resource "azurerm_storage_container" "backup" {
  name               = local.container
  storage_account_id = azurerm_storage_account.backup.id
}

resource "azurerm_storage_container" "backup_lts" {
  name               = local.container_lts
  storage_account_id = azurerm_storage_account.backup.id
}

resource "azurerm_storage_management_policy" "backup" {
  storage_account_id = azurerm_storage_account.backup.id

  rule {
    name    = "tier-and-expire"
    enabled = true
    # #827: legacy tarball prefixes ONLY. This rule must NEVER match the restic repo prefixes
    # (files/, pg/) - a lifecycle delete inside a restic repo corrupts it; the repos manage
    # their own retention via forget/prune.
    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["pg-repo-", "files-repo-"]
    }
    actions {
      base_blob {
        # Tier ladder + expiry for the aging legacy tarballs (restic prefixes never match).
        tier_to_cool_after_days_since_modification_greater_than    = var.cool_after_days
        tier_to_cold_after_days_since_modification_greater_than    = var.cold_after_days
        tier_to_archive_after_days_since_modification_greater_than = var.archive_after_days
        delete_after_days_since_modification_greater_than          = var.delete_after_days
      }
    }
  }
}

output "resource_group" { value = azurerm_resource_group.backup.name }
output "storage_account" { value = azurerm_storage_account.backup.name }
output "container" { value = azurerm_storage_container.backup.name }
output "container_lts" { value = azurerm_storage_container.backup_lts.name }
output "sync_hint" {
  value = "set DOKTOK_AZURE_ACCOUNT=${azurerm_storage_account.backup.name} DOKTOK_AZURE_CONTAINER=${azurerm_storage_container.backup.name} (+ sync SAS rwcl-no-delete as DOKTOK_AZURE_SAS, prune SAS rwcld as DOKTOK_AZURE_SAS_PRUNE host-only)"
}
