"""Host-script tests for the Azure offsite provisioning script (#348, restic transport #827).

Text assertions on deploy/azure-provision.sh: multi-instance naming derivation, the safety
controls (soft-delete instead of container WORM), the lifecycle policy scoped to the legacy
tarball prefixes only (never the restic repos), and the restic repo init + two-SAS guidance.
The live provisioning itself runs against the real subscription from the operator's host.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = (REPO_ROOT / "deploy" / "azure-provision.sh").read_text(encoding="utf-8")


def test_instance_id_derived_names() -> None:
    assert "DOKTOK_INSTANCE_ID" in SCRIPT
    assert "doktok-${instance}-rg" in SCRIPT
    assert "doktokbkp${instance}" in SCRIPT
    # explicit names still win
    assert "DOKTOK_AZURE_RG:-" in SCRIPT
    assert "DOKTOK_AZURE_ACCOUNT:-" in SCRIPT
    assert "DOKTOK_AZURE_CONTAINER:-" in SCRIPT


def test_storage_account_name_length_guard() -> None:
    # Azure hard limit is 24 chars, lowercase+digits, globally unique.
    assert "${#ACCOUNT}" in SCRIPT and "24" in SCRIPT


def test_instance_resources_are_tagged() -> None:
    assert "app=doktok-ng" in SCRIPT
    assert 'instance="$instance"' in SCRIPT
    assert "purpose=backup" in SCRIPT


def test_soft_delete_replaces_worm() -> None:
    # #827: time-based WORM conflicts with restic prune (blocked deletes, 2026-08-18 incident).
    # Ransomware resistance = 30d soft-delete + no-delete sync SAS + host-only prune SAS.
    assert "--enable-delete-retention true" in SCRIPT
    assert "--delete-retention-days" in SCRIPT
    assert "immutability-policy create" not in SCRIPT
    # existing deployments get the old policies removed idempotently
    assert "immutability-policy delete" in SCRIPT


def test_lifecycle_never_touches_the_restic_repos() -> None:
    assert "management-policy create" in SCRIPT
    # expiry applies ONLY to the legacy tarball prefixes; a delete inside files//pg/ prefixes
    # would corrupt the restic repos
    assert '"prefixMatch": ["pg-repo-", "files-repo-"]' in SCRIPT
    assert "tierToArchive" not in SCRIPT  # Archive rehydration takes hours - would blow RTO


def test_restic_repos_are_initialized_and_two_sas_guidance_printed() -> None:
    assert "restic init" in SCRIPT and ":/files" in SCRIPT and ":/pg" in SCRIPT
    assert "DOKTOK_AZURE_SAS" in SCRIPT and "DOKTOK_AZURE_SAS_PRUNE" in SCRIPT
    assert "--permissions rwcl" in SCRIPT  # sync SAS: read/write/create/list, NO delete
