"""Worker watched-set = env token-map UNION active DB tenants, minus suspended ones."""

import os

import pytest
from doktok_contracts.schemas import Tenant
from doktok_core.config import Settings
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_worker.composition import active_tenant_ids


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Settings(_env_file=None) still honors real process env vars; scrub them so a developer's
    # exported .env (e.g. under `make check`) cannot leak into these assertions.
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _settings(**tokens: str) -> Settings:
    return Settings(env="test", tenant_tokens=tokens, _env_file=None)  # type: ignore[call-arg]


def test_env_only_deployment_is_unchanged() -> None:
    reg = InMemoryTenantRegistry()  # empty DB
    assert active_tenant_ids(_settings(tok="developer"), reg) == ["developer"]


def test_db_tenant_is_included_without_env_edit() -> None:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="staging-guid", name="Staging"))  # active by default
    assert active_tenant_ids(_settings(tok="developer"), reg) == ["developer", "staging-guid"]


def test_union_dedups_overlap() -> None:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="developer", name="Developer"))
    assert active_tenant_ids(_settings(tok="developer"), reg) == ["developer"]


def test_suspended_db_tenant_is_excluded_even_if_in_env_map() -> None:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="developer", name="Developer", status="suspended"))
    # Still in the env map, but suspended in the DB -> dropped (status is the kill switch).
    assert active_tenant_ids(_settings(tok="developer"), reg) == []


def test_registry_failure_falls_back_to_env_map() -> None:
    class _Boom(InMemoryTenantRegistry):
        def list_tenants(self) -> list[Tenant]:
            raise RuntimeError("db down")

    assert active_tenant_ids(_settings(tok="developer"), _Boom()) == ["developer"]
