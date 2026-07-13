from pathlib import Path

import pytest
from doktok_core.security.auth import hash_token
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.tenants.provisioning import (
    InvalidTenantId,
    provision_tenant,
    validate_tenant_id,
)


def test_provision_creates_row_folders_and_token(tmp_path: Path) -> None:
    reg = InMemoryTenantRegistry()
    result = provision_tenant(reg, str(tmp_path), name="Staging")

    assert result.created is True
    assert reg.get_tenant(result.tenant_id) is not None
    # Both intake folders exist (usable, not a dead tenant).
    assert (tmp_path / result.tenant_id / "ingest").is_dir()
    assert (tmp_path / result.tenant_id / "ingest.enhanced").is_dir()
    # The bootstrap token is tenant-scoped (no user) and resolves to that tenant.
    assert result.token is not None
    resolution = reg.resolve_token(hash_token(result.token))
    assert resolution is not None
    assert resolution.tenant_id == result.tenant_id
    assert resolution.user_id is None


def test_provision_is_idempotent_on_the_row(tmp_path: Path) -> None:
    reg = InMemoryTenantRegistry()
    first = provision_tenant(reg, str(tmp_path), name="Staging")
    again = provision_tenant(reg, str(tmp_path), name="Staging", tenant_id=first.tenant_id)
    assert again.created is False  # row already existed
    assert len(reg.tenants) == 1  # no duplicate tenant


def test_provision_without_token(tmp_path: Path) -> None:
    result = provision_tenant(InMemoryTenantRegistry(), str(tmp_path), name="X", issue_token=False)
    assert result.token is None


def test_validate_tenant_id_rejects_path_traversal() -> None:
    validate_tenant_id("a-Valid_id-123")  # ok
    for bad in ("../etc", "a/b", "..", "", "a" * 65, "has space", "semi;colon"):
        with pytest.raises(InvalidTenantId):
            validate_tenant_id(bad)


def test_provision_rejects_unsafe_supplied_id(tmp_path: Path) -> None:
    with pytest.raises(InvalidTenantId):
        provision_tenant(InMemoryTenantRegistry(), str(tmp_path), name="X", tenant_id="../escape")
