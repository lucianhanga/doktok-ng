"""Host-script tests for the Azure offsite provisioning script (#348).

Text assertions on deploy/azure-provision.sh: multi-instance naming derivation, the lifecycle
policy (Cool/expire, never Archive), and the safety controls. The live provisioning itself ran
against the real subscription during development of the ticket.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = (REPO_ROOT / "deploy" / "azure-provision.sh").read_text(encoding="utf-8")


def test_instance_id_derived_names() -> None:
    assert "DOKTOK_INSTANCE_ID" in SCRIPT
    assert 'doktok-${instance}-rg' in SCRIPT
    assert 'doktokbkp${instance}' in SCRIPT
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


def test_lifecycle_tiers_to_cool_and_expires_never_archive() -> None:
    assert "management-policy create" in SCRIPT
    assert '"tierToCool"' in SCRIPT
    assert '"delete"' in SCRIPT
    assert "tierToArchive" not in SCRIPT  # Archive rehydration takes hours - would blow RTO
    assert "DOKTOK_AZURE_COOL_AFTER_DAYS" in SCRIPT
    assert "DOKTOK_AZURE_DELETE_AFTER_DAYS" in SCRIPT


def test_immutability_and_versioning_controls() -> None:
    assert "immutability-policy create" in SCRIPT
    assert "--enable-versioning true" in SCRIPT
    assert "--allow-blob-public-access false" in SCRIPT
    assert "DOKTOK_AZURE_RETENTION_DAYS" in SCRIPT
