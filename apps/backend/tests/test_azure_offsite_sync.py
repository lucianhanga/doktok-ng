"""Host-script tests for the Azure offsite sync (tarball design, #345/#347) + Terraform module.

Text assertions on deploy/azure-sync.sh and deploy/terraform/main.tf: the offsite hop ships ONE
tarball per leg (never a raw 68k-file sync), the audit enforces the minimum-set floor, and the
Terraform module owns the infra incl. the full lifecycle ladder.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SYNC = (REPO_ROOT / "deploy" / "azure-sync.sh").read_text(encoding="utf-8")
TF = (REPO_ROOT / "deploy" / "terraform" / "main.tf").read_text(encoding="utf-8")


def test_sync_bundles_one_tarball_per_leg() -> None:
    assert 'tar -czf "$staging/pg-repo-${ts}.tar.gz"' in SYNC
    assert 'tar -czf "$staging/files-repo-${ts}.tar.gz"' in SYNC
    # mutable operational dirs never enter the pg bundle
    assert "--exclude='pg/log'" in SYNC and "--exclude='pg/lock'" in SYNC
    # raw blob sync of the repo tree is gone for good
    assert "az storage blob sync" not in SYNC


def test_sync_uploads_without_overwrite_and_audits_the_floor() -> None:
    assert "--overwrite false" in SYNC  # write-once blobs: fits immutability, no modify conflicts
    assert "DOKTOK_OFFSITE_MIN_SETS" in SYNC
    assert '--prefix "pg-repo-"' in SYNC and '--prefix "files-repo-"' in SYNC
    assert "write_status offsite" in SYNC and "log_event offsite" in SYNC


def test_terraform_owns_the_full_stack() -> None:
    for res in (
        "azurerm_resource_group",
        "azurerm_storage_account",
        "azurerm_storage_container",
        "azurerm_storage_container_immutability_policy",
        "azurerm_storage_management_policy",
    ):
        assert f'resource "{res}"' in TF
    assert "versioning_enabled = true" in TF
    assert "allow_nested_items_to_be_public = false" in TF


def test_terraform_instance_naming_and_tags() -> None:
    assert 'doktok-${var.instance_id}-rg' in TF
    assert 'doktokbkp${var.instance_id}' in TF
    assert '"doktok-backups"' in TF
    assert 'app      = "doktok-ng"' in TF
    assert "purpose  = " in TF and '"backup"' in TF


def test_terraform_lifecycle_ladder() -> None:
    assert "tier_to_cool_after_days_since_modification_greater_than" in TF
    assert "tier_to_cold_after_days_since_modification_greater_than" in TF
    assert "tier_to_archive_after_days_since_modification_greater_than" in TF
    assert "delete_after_days_since_modification_greater_than" in TF
    assert "cool_after_days" in TF and "cold_after_days" in TF
    assert "archive_after_days" in TF and "delete_after_days" in TF


def test_azure_fetch_downloads_unpacks_and_verifies() -> None:
    fetch = (REPO_ROOT / "deploy" / "azure-fetch.sh").read_text(encoding="utf-8")
    assert '--prefix "pg-repo-"' in fetch  # resolves the latest offsite set
    assert '"${leg}-repo-${want_ts}.tar.gz"' in fetch  # both legs, one tarball each
    assert "az storage blob download" in fetch
    assert "tar -xzf" in fetch
    assert "restic snapshots" in fetch  # verifies the fetched restic repo is readable
    assert "--stanza=doktok info" in fetch  # verifies the fetched pgBackRest repo is readable
    # restore-from-staging uses the SAME engine, pointed at the staging dir
    assert "DOKTOK_BACKUP_DIR=${staging}" in fetch
