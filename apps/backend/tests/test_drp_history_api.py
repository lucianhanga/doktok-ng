"""DRP backup history + on-demand drill endpoints (M12 DRP hardening)."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository, AuditLogRepository
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
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


def _build(
    repo: InMemoryAppSettingsRepository, **settings_kw: object
) -> tuple[TestClient, InMemoryAuditLogRepository]:
    registry = build_registry()
    registry.register(AppSettingsRepository, repo)  # type: ignore[type-abstract]
    audit = InMemoryAuditLogRepository()
    registry.register(AuditLogRepository, audit)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None, **settings_kw)  # type: ignore[arg-type,call-arg]
    return TestClient(create_app(settings=settings, registry=registry)), audit


def _chained(records: list[tuple[str, str, bool]]) -> list[str]:
    """Build valid prev_sha256-chained JSONL lines from (leg, event, ok) tuples (oldest-first)."""
    lines: list[str] = []
    prev = ""
    for i, (leg, event, ok) in enumerate(records, start=1):
        line = json.dumps(
            {
                "schema": 1,
                "seq": i,
                "prev_sha256": prev,
                "ts": f"2026-06-26T03:00:{i:02d}Z",
                "leg": leg,
                "event": event,
                "ok": ok,
                "detail": "snapshot",
            }
        )
        lines.append(line)
        prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
    return lines


# --- history -------------------------------------------------------------------------------------


def test_history_requires_auth() -> None:
    client, _ = _build(InMemoryAppSettingsRepository())
    assert client.get("/api/v1/settings/drp/history").status_code == 401


def test_history_empty_on_fresh_install() -> None:
    client, _ = _build(InMemoryAppSettingsRepository())
    body = client.get("/api/v1/settings/drp/history", headers=AUTH).json()
    assert body["events"] == []
    assert body["source_available"] is False
    assert body["total_returned"] == 0
    assert body["integrity_ok"] is True


def test_history_bad_leg_is_422() -> None:
    client, _ = _build(InMemoryAppSettingsRepository())
    resp = client.get("/api/v1/settings/drp/history?leg=bogus", headers=AUTH)
    assert resp.status_code == 422


def test_history_newest_first_and_filtered() -> None:
    repo = InMemoryAppSettingsRepository()
    repo.backup_history_lines = _chained(
        [("files", "success", True), ("pg", "success", True), ("files", "failure", False)]
    )
    client, _ = _build(repo)
    body = client.get("/api/v1/settings/drp/history", headers=AUTH).json()
    assert body["source_available"] is True
    assert [e["leg"] for e in body["events"]] == ["files", "pg", "files"]  # newest-first
    assert body["integrity_ok"] is True
    # leg filter
    files_only = client.get("/api/v1/settings/drp/history?leg=files", headers=AUTH).json()
    assert {e["leg"] for e in files_only["events"]} == {"files"}


def test_history_integrity_false_on_broken_chain() -> None:
    repo = InMemoryAppSettingsRepository()
    lines = _chained([("files", "success", True), ("pg", "success", True)])
    bad = json.loads(lines[1])
    bad["prev_sha256"] = "deadbeef"
    lines[1] = json.dumps(bad)
    repo.backup_history_lines = lines
    client, _ = _build(repo)
    body = client.get("/api/v1/settings/drp/history", headers=AUTH).json()
    assert body["integrity_ok"] is False


def test_history_never_exposes_chain_fields() -> None:
    repo = InMemoryAppSettingsRepository()
    repo.backup_history_lines = _chained([("files", "success", True)])
    client, _ = _build(repo)
    resp = client.get("/api/v1/settings/drp/history", headers=AUTH)
    assert "prev_sha256" not in resp.text and '"schema"' not in resp.text


def test_history_mirror_inserts_once_and_is_idempotent() -> None:
    repo = InMemoryAppSettingsRepository()
    repo.backup_history_lines = _chained(
        [("files", "success", True), ("pg", "failure", False), ("files", "start", True)]
    )
    client, audit = _build(repo)
    # First read mirrors the two terminal events (start is skipped as noise).
    client.get("/api/v1/settings/drp/history", headers=AUTH)
    first = audit.list_events("tenant-a", limit=100)
    mirrored = [e for e in first if e.metadata.get("source") == "drp"]
    assert len(mirrored) == 2
    assert all(e.metadata.get("authoritative") is False for e in mirrored)
    # A second read of the same window must NOT duplicate rows (deterministic id collapses them).
    client.get("/api/v1/settings/drp/history", headers=AUTH)
    second = [
        e for e in audit.list_events("tenant-a", limit=100) if e.metadata.get("source") == "drp"
    ]
    assert len(second) == 2


# --- drill trigger -------------------------------------------------------------------------------


def test_drill_requires_auth(tmp_path: Path) -> None:
    client, _ = _build(InMemoryAppSettingsRepository(), backup_dir=str(tmp_path))
    assert client.post("/api/v1/settings/drp/drill").status_code == 401


def test_drill_writes_request_file_once_then_429(tmp_path: Path) -> None:
    repo = InMemoryAppSettingsRepository()
    client, _ = _build(repo, backup_dir=str(tmp_path))
    resp = client.post("/api/v1/settings/drp/drill", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True
    req_file = tmp_path / "status" / "requests" / "drill.request"
    assert req_file.is_file()
    payload = json.loads(req_file.read_text())
    assert payload["actor"] == "tenant-a" and "requested_at" in payload
    # A second request while one is pending is rate-limited.
    second = client.post("/api/v1/settings/drp/drill", headers=AUTH)
    assert second.status_code == 429


def test_drill_429_when_recent_drill(tmp_path: Path) -> None:
    repo = InMemoryAppSettingsRepository()
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    repo.backup_status = {"drill": {"ok": True, "last_run_at": now, "detail": "drill"}}
    client, _ = _build(repo, backup_dir=str(tmp_path))
    resp = client.post("/api/v1/settings/drp/drill", headers=AUTH)
    assert resp.status_code == 429


def test_drill_allowed_after_cooldown(tmp_path: Path) -> None:
    repo = InMemoryAppSettingsRepository()
    old = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    repo.backup_status = {"drill": {"ok": True, "last_run_at": old, "detail": "drill"}}
    client, _ = _build(repo, backup_dir=str(tmp_path))
    resp = client.post("/api/v1/settings/drp/drill", headers=AUTH)
    assert resp.status_code == 200
