"""Operational-read gating (#633, security audit F-19).

Settings reads and /metrics were readable by ANY authenticated caller (a viewer could map host
paths, backup cadence, model topology, and hardware), and /audit exposed every user's login
email/IP and admin actions. Now: settings + metrics require the admin role, and the audit feed is
scoped for non-admins to document/entity activity (no auth/user/ops events).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository, AuditLogRepository, TenantRegistry
from doktok_contracts.schemas import AuditEvent, Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

JWT_SECRET = "f19-test-secret"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _bearer(user_id: str) -> dict[str, str]:
    token = issue_access_token(
        tenant_id="tenant-a", user_id=user_id, secret=JWT_SECRET, ttl_seconds=3600
    )
    return {"Authorization": f"Bearer {token}"}


def _client(tmp_path: Path, *, events: list[AuditEvent] | None = None) -> TestClient:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="Tenant A"))
    for uid, role in (("u_view", "viewer"), ("u_edit", "editor"), ("u_admin", "admin")):
        reg.create_user(User(id=uid, tenant_id="tenant-a", email=f"{uid}@x.com", role=role))
    audit = InMemoryAuditLogRepository()
    for event in events or []:
        audit.record(event)
    registry = build_registry()
    registry.register(TenantRegistry, reg)  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, audit)  # type: ignore[type-abstract]
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        auth_jwt_secret=JWT_SECRET,
        tenant_tokens={"tok-admin": "tenant-a"},
        files_root=str(tmp_path),
        backup_dir=str(tmp_path / "backups"),
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry))


# --- settings + metrics: admin-only now (F-19) ---


def test_settings_reads_require_admin(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for path in ("/api/v1/settings/ai", "/api/v1/settings/ocr", "/api/v1/settings/drp"):
        assert client.get(path, headers=_bearer("u_view")).status_code == 403, path
        assert client.get(path, headers=_bearer("u_edit")).status_code == 403, path
        assert client.get(path, headers=_bearer("u_admin")).status_code == 200, path


def test_metrics_requires_admin(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/metrics", headers=_bearer("u_view")).status_code == 403
    assert client.get("/metrics", headers=_bearer("u_edit")).status_code == 403
    assert client.get("/metrics", headers=_bearer("u_admin")).status_code == 200


# --- /audit: document/entity activity for everyone; auth/user/ops events admin-only ---


def _mixed_events() -> list[AuditEvent]:
    def ev(event_type: str, i: int) -> AuditEvent:
        return AuditEvent(
            id=f"e-{event_type}-{i}",
            tenant_id="tenant-a",
            event_type=event_type,
            actor="worker",
            document_id="doc-1",
            timestamp=datetime.now(UTC),
        )

    return [
        ev("document.activated", 1),
        ev("feature.completed", 2),
        ev("entity.merged", 3),
        ev("auth.login_failed", 4),
        ev("user.created", 5),
        ev("settings.changed", 6),
        ev("backup.completed", 7),
    ]


def test_viewer_audit_feed_excludes_auth_user_and_ops_events(tmp_path: Path) -> None:
    client = _client(tmp_path, events=_mixed_events())
    rows = client.get("/api/v1/audit", headers=_bearer("u_view")).json()
    types = {r["event_type"] for r in rows}
    assert "document.activated" in types
    assert "feature.completed" in types
    assert "entity.merged" in types
    assert "auth.login_failed" not in types
    assert "user.created" not in types
    assert "settings.changed" not in types
    assert "backup.completed" not in types


def test_admin_audit_feed_sees_everything(tmp_path: Path) -> None:
    client = _client(tmp_path, events=_mixed_events())
    rows = client.get("/api/v1/audit", headers=_bearer("u_admin")).json()
    types = {r["event_type"] for r in rows}
    assert {"document.activated", "auth.login_failed", "user.created", "backup.completed"} <= types
