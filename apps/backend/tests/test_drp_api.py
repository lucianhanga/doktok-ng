"""Read-only DRP (Disaster Recovery Plan) settings endpoint (#368)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}
AUTH = {"Authorization": "Bearer tok-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _client(repo: InMemoryAppSettingsRepository, **settings_kw: object) -> TestClient:
    registry = build_registry()
    registry.register(AppSettingsRepository, repo)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None, **settings_kw)  # type: ignore[arg-type,call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def test_drp_requires_auth() -> None:
    assert _client(InMemoryAppSettingsRepository()).get("/api/v1/settings/drp").status_code == 401


def test_unknown_when_no_status_source() -> None:
    body = _client(InMemoryAppSettingsRepository()).get("/api/v1/settings/drp", headers=AUTH).json()
    assert body["read_only"] is True
    assert body["status"]["status_source_available"] is False
    assert body["status"]["files"]["state"] == "unknown"


def test_states_derived_from_sentinels() -> None:
    now = datetime.now(UTC)
    repo = InMemoryAppSettingsRepository()
    repo.backup_status = {
        "files": {"ok": True, "last_run_at": _iso(now), "detail": "restic"},
        "pg": {"ok": False, "last_run_at": _iso(now), "detail": "failed"},
        "offsite": {"ok": True, "last_run_at": _iso(now - timedelta(hours=10)), "detail": "sync"},
    }
    body = _client(repo).get("/api/v1/settings/drp", headers=AUTH).json()
    s = body["status"]
    assert s["status_source_available"] is True
    assert s["files"]["state"] == "ok"
    assert s["pg"]["state"] == "failed"
    assert s["offsite"]["state"] == "stale"  # 10h > 3x the 1h offsite RPO
    assert s["drill"]["state"] == "unknown"  # absent leg


def test_backup_metrics_surfaced_from_sentinel() -> None:
    """size/file_count/backup_id captured by the backup scripts pass through to the DRP (#380)."""
    now = datetime.now(UTC)
    repo = InMemoryAppSettingsRepository()
    repo.backup_status = {
        "files": {
            "ok": True,
            "last_run_at": _iso(now),
            "detail": "restic snapshot",
            "size": "662 MiB",
            "file_count": 287,
            "backup_id": "a1b2c3d4e5f60718",
        },
        "pg": {
            "ok": True,
            "last_run_at": _iso(now),
            "detail": "pgbackrest full",
            "size": "1.2 GiB",
            "backup_id": "20260625-120000F",
        },
    }
    s = _client(repo).get("/api/v1/settings/drp", headers=AUTH).json()["status"]
    assert s["files"]["size"] == "662 MiB"
    assert s["files"]["file_count"] == 287
    assert s["files"]["backup_id"] == "a1b2c3d4e5f60718"
    assert s["pg"]["size"] == "1.2 GiB"
    assert s["pg"]["file_count"] is None  # pg has no file count
    assert s["pg"]["backup_id"] == "20260625-120000F"


def test_config_booleans_no_secret_values() -> None:
    repo = InMemoryAppSettingsRepository()
    client = _client(
        repo,
        backup_dir="/var/lib/doktok/backups",
        deploy_mode="compose",
        azure_container="doktok-backups",
        azure_immutable=True,
        restic_password="rp",  # pragma: allowlist secret
        pgbackrest_cipher_pass="pp",
        azure_sas="sas-value",
    )
    resp = client.get("/api/v1/settings/drp", headers=AUTH)
    body = resp.json()
    cfg = body["config"]
    assert cfg["repo_location"] == "/var/lib/doktok/backups"
    assert cfg["deploy_mode"] == "compose"
    assert cfg["azure_container"] == "doktok-backups" and cfg["immutability_enabled"] is True
    assert cfg["encryption_keys_configured"] is True and cfg["azure_credentials_configured"] is True
    # The actual secret value must never appear anywhere in the response - only the boolean.
    assert "sas-value" not in resp.text
