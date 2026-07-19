"""Least-privilege machine tokens (#645, security audit F-33).

A user-less api_token previously resolved to Role.ADMIN unconditionally - a leaked script token
was a full tenant compromise. Tokens now carry a role (viewer|editor|admin) enforced at
resolution; minting defaults to viewer (least privilege). Static host-provisioned tokens stay
admin (the platform tier); user-bound tokens keep resolving through the user's registry role.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AuditLogRepository, ChatThreadRepository, TenantRegistry
from doktok_contracts.schemas import Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.chat.inmemory import InMemoryChatThreadRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.inmemory import InMemoryTenantRegistry
from fastapi.testclient import TestClient

ADMIN = {"Authorization": "Bearer tok-admin"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client(tmp_path: Path) -> TestClient:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="Tenant A"))
    reg.create_user(User(id="u_admin", tenant_id="tenant-a", email="admin@x.com", role="admin"))
    registry = build_registry()
    registry.register(TenantRegistry, reg)  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    registry.register(ChatThreadRepository, InMemoryChatThreadRepository())  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        tenant_tokens={"tok-admin": "tenant-a"},
        files_root=str(tmp_path),
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry))


def _mint(client: TestClient, **fields: object) -> str:
    resp = client.post("/api/v1/admin/tokens", json=fields, headers=ADMIN)
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]  # type: ignore[no-any-return]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _read(client: TestClient, token: str) -> int:
    resp = client.get("/api/v1/chat/threads", headers=_auth(token))
    return int(resp.status_code)


def _write(client: TestClient, token: str) -> int:
    resp = client.post(
        "/api/v1/ingestion/upload",
        files=[("files", ("a.txt", b"x", "text/plain"))],
        headers=_auth(token),
    )
    return int(resp.status_code)


def _admin_read(client: TestClient, token: str) -> int:
    resp = client.get("/api/v1/admin/users", headers=_auth(token))
    return int(resp.status_code)


def test_userless_viewer_token_reads_but_cannot_write_or_admin(tmp_path: Path) -> None:
    client = _client(tmp_path)
    token = _mint(client, name="ro", role="viewer")
    assert _read(client, token) == 200
    assert _write(client, token) == 403
    assert _admin_read(client, token) == 403


def test_userless_editor_token_writes_but_is_not_admin(tmp_path: Path) -> None:
    client = _client(tmp_path)
    token = _mint(client, name="ci", role="editor")
    assert _write(client, token) == 200
    assert _admin_read(client, token) == 403


def test_userless_admin_token_keeps_full_access(tmp_path: Path) -> None:
    client = _client(tmp_path)
    token = _mint(client, name="ops", role="admin")
    assert _admin_read(client, token) == 200


def test_mint_without_role_defaults_to_viewer(tmp_path: Path) -> None:
    client = _client(tmp_path)
    token = _mint(client, name="defaulted")
    assert _write(client, token) == 403  # least privilege by default
    listed = client.get("/api/v1/admin/tokens", headers=ADMIN).json()
    assert listed[0]["role"] == "viewer"


def test_user_bound_token_uses_the_users_own_role(tmp_path: Path) -> None:
    # role on the token row governs MACHINE (user-less) tokens only; a user-bound token still
    # resolves through the user's registry role (u_admin here).
    client = _client(tmp_path)
    token = _mint(client, name="bound", user_id="u_admin", role="viewer")
    assert _admin_read(client, token) == 200


def test_invalid_role_is_422(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/v1/admin/tokens", json={"name": "x", "role": "root"}, headers=ADMIN)
    assert resp.status_code == 422
