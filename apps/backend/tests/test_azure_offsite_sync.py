"""Host-script tests for the Azure offsite fetch (restic transport, #827) + Terraform module.

Text assertions on deploy/azure-fetch.sh and deploy/terraform/main.tf: fetch rebuilds both repos
from the Azure restic repos into the staging layout the restore engine consumes, and the
Terraform module owns the infra with soft-delete instead of container WORM and the lifecycle
rule scoped to the legacy tarball prefixes only (#827). The legacy tarball-sync assertions are
superseded by test_azure_restic_transport.py (#827).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TF = (REPO_ROOT / "deploy" / "terraform" / "main.tf").read_text(encoding="utf-8")


def test_terraform_owns_the_full_stack() -> None:
    for res in (
        "azurerm_resource_group",
        "azurerm_storage_account",
        "azurerm_storage_container",
        "azurerm_storage_management_policy",
    ):
        assert f'resource "{res}"' in TF
    assert "versioning_enabled = true" in TF
    assert "allow_nested_items_to_be_public = false" in TF
    # #827: soft-delete replaces container WORM (conflicts with restic prune)
    assert "delete_retention_policy" in TF
    assert "azurerm_storage_container_immutability_policy" not in TF


def test_terraform_instance_naming_and_tags() -> None:
    assert "doktok-${var.instance_id}-rg" in TF
    assert "doktokbkp${var.instance_id}" in TF
    assert '"doktok-backups"' in TF
    assert 'app      = "doktok-ng"' in TF
    assert "purpose  = " in TF and '"backup"' in TF


def test_terraform_lifecycle_scoped_to_legacy_tarballs() -> None:
    assert 'prefix_match = ["pg-repo-", "files-repo-"]' in TF
    assert "delete_after_days_since_modification_greater_than" in TF


def test_azure_fetch_restores_from_the_restic_repos() -> None:
    fetch = (REPO_ROOT / "deploy" / "azure-fetch.sh").read_text(encoding="utf-8")
    # files leg: restore the tree from Azure, then rebuild a local restic repo from it (the
    # restore engine consumes staging/files as a repo; single-repo auth only - no restic copy)
    assert "restore latest" in fetch and "restic init" in fetch
    # pg leg: restic restore of the pg-repo snapshot, then pgBackRest runs against the result
    assert "--stanza=doktok info" in fetch  # fetched pgBackRest repo verified readable
    # the staging contract the restore engine consumes is unchanged
    assert "DOKTOK_BACKUP_DIR=${staging}" in fetch
    # no tarball machinery left
    assert "az storage blob download" not in fetch
    # compose mode restores+rebuilds the files repo entirely inside the runner on
    # container-local storage (virtiofs O_NOATIME/EIO workaround, same class as the sync legs)
    assert "/tmp/filestree" in fetch
    # the pg repo root is located structurally (the dir holding `archive`), never by a
    # name/depth assumption - the sync stages it at /tmp/pg-repo, which has no `/pg/` segment
    assert "'*/pg/*'" not in fetch
    assert "-name archive -print -quit" in fetch
