"""Endpoint tests for the platform-owner guard on the portable backup export (#613, audit F-01).

The export surface is deployment-global (a full pg_dump of every tenant + the whole files tree), so
it is gated to platform admins (ADR-0025): host-provisioned static tokens and users flagged
``is_platform_admin`` (with the admin role). Tenant admins - including DB-minted user-less api
tokens, which any tenant admin can issue - get 403.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository, AuditLogRepository, TenantRegistry
from doktok_contracts.schemas import ApiToken, Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.backup import export as export_mod
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.auth import hash_token
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

STATIC = {"Authorization": "Bearer tok-static"}  # the host credential (console)
USERLESS = {"Authorization": "Bearer tok-userless"}  # DB api token, no user -> tenant admin only
ADMIN = {"Authorization": "Bearer tok-admin"}  # user, admin role -> tenant admin only

_REAL_RUN = subprocess.run


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    argv = args[0] if args else kwargs.get("args")
    assert isinstance(argv, list)
    if argv and argv[0] == "pg_dump":
        Path(argv[argv.index("-f") + 1]).write_bytes(b"PGDMP-fake\x00\x01")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    if argv and argv[0] == "psql":
        return subprocess.CompletedProcess(argv, 0, stdout="17.2\n", stderr="")
    return _REAL_RUN(*args, **kwargs)


def _tenant_registry() -> InMemoryTenantRegistry:
    mem = InMemoryTenantRegistry()
    mem.create_tenant(Tenant(id="tenant-b", name="B"))
    for uid, role in (("admin-b", "admin"),):
        mem.create_user(
            User(id=uid, tenant_id="tenant-b", email=f"{uid}@b.example", role=role, status="active")
        )
    token_pairs: list[tuple[str, str | None]] = [
        ("tok-userless", None),
        ("tok-admin", "admin-b"),
    ]
    for token, owner in token_pairs:
        mem.create_api_token(
            ApiToken(id=token, tenant_id="tenant-b", user_id=owner, token_sha256=hash_token(token))
        )
    return mem


def _make_client(tmp_path: Path) -> TestClient:
    files_root = tmp_path / "files"
    files_root.mkdir(parents=True)
    registry = build_registry()
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    registry.register(TenantRegistry, _tenant_registry())  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        tenant_tokens={"tok-static": "tenant-a"},
        files_root=str(files_root),
        backup_export_dir=str(tmp_path / "exports"),
        secrets_key="unit-secret",  # pragma: allowlist secret
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry))


def test_static_token_is_platform_admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_mod.subprocess, "run", _fake_run)
    client = _make_client(tmp_path)
    assert client.post("/api/v1/settings/backup/export", headers=STATIC).status_code == 200


def test_db_userless_token_is_not_platform_admin(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    assert client.post("/api/v1/settings/backup/export", headers=USERLESS).status_code == 403


def test_tenant_admin_without_flag_is_not_platform_admin(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    assert client.post("/api/v1/settings/backup/export", headers=ADMIN).status_code == 403


def test_export_status_read_is_platform_gated(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    # Tenant admins (and viewers) no longer read export metadata; platform admins get 404
    # (no builds yet).
    assert client.get("/api/v1/settings/backup/export/status", headers=ADMIN).status_code == 403
    assert client.get("/api/v1/settings/backup/export/status", headers=USERLESS).status_code == 403
    assert client.get("/api/v1/settings/backup/export/status", headers=STATIC).status_code == 404


def test_export_download_is_platform_gated(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    body = {"passphrase": "download-pass"}
    resp = client.post("/api/v1/settings/backup/export/x/download", headers=ADMIN, json=body)
    assert resp.status_code == 403
