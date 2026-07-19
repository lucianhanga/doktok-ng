"""TenantRegistry startup pre-warm (#637, security audit F-23).

The DB-backed TenantRegistry was built lazily on the first auth/admin request; until then the
per-request deactivation check was skipped, so a deactivated user's unexpired JWT kept read access
after a backend restart - potentially for hours on a quiet box. The registry is now registered
eagerly at startup (outside tests), sharing one pool via ``app.state.database``; a startup failure
never blocks boot (the lazy path remains as fallback).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import TenantRegistry
from doktok_core.config import Settings
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _fake_storage(monkeypatch: pytest.MonkeyPatch) -> object:
    """Stub the storage layer so the startup warm runs without a live database."""
    import doktok_core.settings.bootstrap as bootstrap
    import doktok_storage_postgres as sp

    class _FakeDb:
        def close(self) -> None:  # the lifespan closes app.state.database on shutdown
            pass

    fake_db = _FakeDb()
    monkeypatch.setattr(sp, "Database", lambda *a, **k: fake_db)
    monkeypatch.setattr(sp, "migrate", lambda db: None)
    monkeypatch.setattr(bootstrap, "seed_ai_settings", lambda repo, settings: None)
    return fake_db


def _dev_settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        env="dev",
        database_url="postgresql://unused",
        tenant_tokens={"tok-a": "tenant-a"},
        files_root=str(tmp_path),
        _env_file=None,
    )


def test_tenant_registry_is_warmed_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_db = _fake_storage(monkeypatch)
    app = create_app(settings=_dev_settings(tmp_path))
    # The lifespan only runs when the TestClient is used as a context manager.
    with TestClient(app):
        assert app.state.registry.is_registered(TenantRegistry)
        assert app.state.database is fake_db  # request-time resolution reuses the same pool


def test_warm_failure_does_not_block_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import doktok_storage_postgres as sp

    def _boom(*a: object, **k: object) -> object:
        raise RuntimeError("db down")

    monkeypatch.setattr(sp, "Database", _boom)
    app = create_app(settings=_dev_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    # The lazy request-time path remains as the fallback.
    assert not app.state.registry.is_registered(TenantRegistry)


def test_test_env_is_not_warmed() -> None:
    app = create_app(
        settings=Settings(env="test", tenant_tokens={"tok-a": "tenant-a"}, _env_file=None)  # type: ignore[call-arg]
    )
    with TestClient(app):
        assert not app.state.registry.is_registered(TenantRegistry)
