# DokTok NG offsite backup infrastructure (Azure Blob) as code (#348/#345/#347).
#
# One deployment per doktok-ng instance. All names derive from var.instance_id (12 hex chars,
# persisted in the instance's .env as DOKTOK_INSTANCE_ID) so independent instances never collide:
#   resource group   doktok-<id>-rg
#   storage account  doktokbkp<id>     (Azure: lowercase+digits, 3-24 chars, globally unique)
#   container        doktok-backups
#
# Controls: blob versioning + a 30-day immutability policy (ransomware/WORM) + a lifecycle ladder
# that tiers old blobs to cheaper storage and expires them (cost control). Blobs are write-once
# tarballs produced by deploy/azure-sync.sh, which fits immutability exactly.
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

variable "retention_days" {
  type        = number
  default     = 30
  description = "Immutability window (days): blobs cannot be modified/deleted inside it (WORM)."
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
  default     = 365
  description = "Expire blobs past this age. The minimum-COUNT floor cannot be expressed in a lifecycle rule; deploy/azure-sync.sh enforces it operationally (audit)."
}

locals {
  rg        = "doktok-${var.instance_id}-rg"
  account   = "doktokbkp${var.instance_id}"
  container = "doktok-backups"
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
  }
}

resource "azurerm_storage_container" "backup" {
  name               = local.container
  storage_account_id = azurerm_storage_account.backup.id
}

resource "azurerm_storage_container_immutability_policy" "backup" {
  storage_container_resource_manager_id = azurerm_storage_container.backup.id
  immutability_period_in_days           = var.retention_days
  protected_append_writes_enabled       = true
  # Unlocked on purpose: keep it extensible while the setup matures. Lock it (locked = true) only
  # deliberately - a locked policy can never be shortened/removed.
  locked = false
}

resource "azurerm_storage_management_policy" "backup" {
  storage_account_id = azurerm_storage_account.backup.id

  rule {
    name    = "tier-and-expire"
    enabled = true
    filters {
      blob_types = ["blockBlob"]
    }
    actions {
      base_blob {
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
output "sync_hint" {
  value = "set DOKTOK_AZURE_ACCOUNT=${azurerm_storage_account.backup.name} DOKTOK_AZURE_CONTAINER=${azurerm_storage_container.backup.name} (+ a write-scoped SAS) in the instance env"
}
