"""Endpoint tests for the portable one-file backup RESTORE (M12 portable restore, Phase 2).

Covers: auth on all three endpoints; the preview streams the multipart upload to disk + decrypts +
validates (wrong passphrase fails cleanly, the passphrase never reaches the logs); the 413 over
DOKTOK_MAX_RESTORE_GB; the 422 on a missing passphrase; the apply requires confirm + a validated
staged_id, is single-flight, and drops the request file exactly once; the status poll reads the
host-written sentinel. pg_dump/psql are mocked; openssl runs for real (the openssl-dependent tests
self-skip when it is absent)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository, AuditLogRepository
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.backup import export as export_mod
from doktok_core.backup import restore as restore_mod
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}
AUTH = {"Authorization": "Bearer tok-a"}
_FAKE_DUMP = b"PGDMP-fake-custom-format-dump\x00\x01\x02"
_SECRET = "unit-secret"  # pragma: allowlist secret
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
        Path(argv[argv.index("-f") + 1]).write_bytes(_FAKE_DUMP)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    if argv and argv[0] == "psql":
        return subprocess.CompletedProcess(argv, 0, stdout="17.2\n", stderr="")
    return _REAL_RUN(*args, **kwargs)


def _make_client(
    tmp_path: Path, *, max_restore_gb: int = 50
) -> tuple[TestClient, InMemoryAuditLogRepository, Path, Path]:
    export_dir = tmp_path / "exports"
    backup_dir = tmp_path / "backups"
    files_root = tmp_path / "files"
    files_root.mkdir(parents=True)
    (files_root / "doc.txt").write_text("payload", encoding="utf-8")
    registry = build_registry()
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    audit = InMemoryAuditLogRepository()
    registry.register(AuditLogRepository, audit)  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        tenant_tokens=TOKENS,
        files_root=str(files_root),
        backup_dir=str(backup_dir),
        backup_export_dir=str(export_dir),
        secrets_key=_SECRET,
        max_restore_gb=max_restore_gb,
        _env_file=None,
    )
    client = TestClient(create_app(settings=settings, registry=registry))
    return client, audit, export_dir, backup_dir


def _build_encrypted_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, passphrase: str, *, secret: str = _SECRET
) -> bytes:
    """Build a real archive (pg_dump mocked) + openssl-encrypt it; return the ciphertext bytes."""
    monkeypatch.setattr(export_mod.subprocess, "run", _fake_run)
    export_dir = tmp_path / "build"
    files_root = tmp_path / "buildfiles"
    files_root.mkdir(parents=True)
    (files_root / "doc.txt").write_text("payload", encoding="utf-8")
    paths = export_mod.ExportPaths(
        export_dir=export_dir,
        files_root=files_root,
        database_url="postgresql://doktok:doktok@db:5432/doktok",  # pragma: allowlist secret
        secrets_key=secret,
        app_version="0.2.0",
        app_schema_version=5,
    )
    info = export_mod.build_export(paths, "b1")
    assert info.status == "ready"
    staged = export_mod.staged_archive_path(export_dir, "b1")
    enc = tmp_path / "cipher.enc"
    proc = _REAL_RUN(
        export_mod.encrypt_argv(staged, enc),
        input=(passphrase + "\n").encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return enc.read_bytes()


# --------------------------------------------------------------------------------------------------
# Auth + validation
# --------------------------------------------------------------------------------------------------


def test_requires_auth(tmp_path: Path) -> None:
    client, _, _, _ = _make_client(tmp_path)
    assert client.post("/api/v1/settings/backup/restore/preview").status_code == 401
    assert (
        client.post("/api/v1/settings/backup/restore/x/apply", json={"confirm": True}).status_code
        == 401
    )
    assert client.get("/api/v1/settings/backup/restore/status").status_code == 401


def test_preview_missing_passphrase_is_422(tmp_path: Path) -> None:
    client, _, _, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/settings/backup/restore/preview",
        files={"file": ("a.enc", b"ciphertext", "application/octet-stream")},
        headers=AUTH,
    )
    assert resp.status_code == 422


def test_preview_short_passphrase_is_422(tmp_path: Path) -> None:
    client, _, _, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/settings/backup/restore/preview",
        files={"file": ("a.enc", b"ciphertext", "application/octet-stream")},
        data={"passphrase": "short"},  # < 8 chars
        headers=AUTH,
    )
    assert resp.status_code == 422


def test_preview_over_size_limit_is_413(tmp_path: Path) -> None:
    # 0 GB cap is bumped to a 1-byte floor in the route -> any real upload exceeds it.
    client, _, export_dir, _ = _make_client(tmp_path, max_restore_gb=0)
    resp = client.post(
        "/api/v1/settings/backup/restore/preview",
        files={
            "file": ("a.enc", b"this is definitely more than one byte", "application/octet-stream")
        },
        data={"passphrase": "a-long-enough-passphrase"},
        headers=AUTH,
    )
    assert resp.status_code == 413
    # Staging was cleaned up after the 413.
    assert not restore_mod.restores_root(export_dir).exists() or not any(
        restore_mod.restores_root(export_dir).iterdir()
    )


# --------------------------------------------------------------------------------------------------
# Preview: full decrypt + validate path
# --------------------------------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_preview_valid_archive_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    passphrase = "correct horse battery staple"  # pragma: allowlist secret
    ciphertext = _build_encrypted_archive(tmp_path, monkeypatch, passphrase)
    client, audit, export_dir, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/settings/backup/restore/preview",
        files={"file": ("backup.tgz.enc", ciphertext, "application/octet-stream")},
        data={"passphrase": passphrase},
        headers=AUTH,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["compatible"] is True
    assert body["errors"] == []
    assert body["secrets_key_match"] is True
    assert body["member_count"] == 3  # db.dump + manifest.json + files/doc.txt
    assert body["pg_version"] == "17.2"
    assert restore_mod.is_validated(export_dir, body["staged_id"]) is True
    # The preview was audited.
    events = audit.list_events("tenant-a", limit=100)
    assert any(e.event_type == "restore.previewed" for e in events)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_preview_wrong_passphrase_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ciphertext = _build_encrypted_archive(tmp_path, monkeypatch, "the real passphrase")
    client, _, export_dir, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/settings/backup/restore/preview",
        files={"file": ("backup.tgz.enc", ciphertext, "application/octet-stream")},
        data={"passphrase": "the WRONG passphrase"},
        headers=AUTH,
    )
    assert resp.status_code == 200  # validation verdict is in the body, not an HTTP error
    body = resp.json()
    assert body["ok"] is False
    assert any("decrypt" in e for e in body["errors"])
    assert restore_mod.is_validated(export_dir, body["staged_id"]) is False


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_preview_passphrase_never_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "super-secret-passphrase-value"  # pragma: allowlist secret
    ciphertext = _build_encrypted_archive(tmp_path, monkeypatch, secret)
    client, _, _, _ = _make_client(tmp_path)
    with caplog.at_level(logging.DEBUG):
        resp = client.post(
            "/api/v1/settings/backup/restore/preview",
            files={"file": ("backup.tgz.enc", ciphertext, "application/octet-stream")},
            data={"passphrase": secret},
            headers=AUTH,
        )
    assert resp.status_code == 200
    assert secret not in caplog.text


def test_preview_garbage_upload_fails_cleanly(tmp_path: Path) -> None:
    client, _, export_dir, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/settings/backup/restore/preview",
        files={
            "file": ("a.enc", b"not a valid encrypted archive at all", "application/octet-stream")
        },
        data={"passphrase": "a-long-enough-passphrase"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


# --------------------------------------------------------------------------------------------------
# Apply: confirm + validated-staged-id + single-flight + request-file
# --------------------------------------------------------------------------------------------------


def test_apply_requires_confirm(tmp_path: Path) -> None:
    client, _, _, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/settings/backup/restore/anything/apply", json={"confirm": False}, headers=AUTH
    )
    assert resp.status_code == 422


def test_apply_unknown_staged_id_is_409(tmp_path: Path) -> None:
    client, _, _, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/settings/backup/restore/nope/apply", json={"confirm": True}, headers=AUTH
    )
    assert resp.status_code == 409


def _stage_validated(export_dir: Path, staged_id: str) -> None:
    """Simulate a staged_id that already passed preview (drop the .validated marker)."""
    sdir = restore_mod.staging_dir(export_dir, staged_id)
    (sdir / "extracted").mkdir(parents=True, exist_ok=True)
    restore_mod._mark_validated(sdir)


def test_apply_drops_request_file_once_and_is_single_flight(tmp_path: Path) -> None:
    client, audit, export_dir, backup_dir = _make_client(tmp_path)
    staged_id = "validated-1"
    _stage_validated(export_dir, staged_id)

    resp = client.post(
        f"/api/v1/settings/backup/restore/{staged_id}/apply", json={"confirm": True}, headers=AUTH
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True and body["restore_id"]
    request_file = backup_dir / "status" / "requests" / "restore.request"
    assert request_file.is_file()
    payload = request_file.read_text()
    assert staged_id in payload
    # The request body never carries a passphrase or DSN.
    assert "passphrase" not in payload and "postgresql://" not in payload

    # A second apply while the first is pending/applying is single-flighted with 409.
    resp2 = client.post(
        f"/api/v1/settings/backup/restore/{staged_id}/apply", json={"confirm": True}, headers=AUTH
    )
    assert resp2.status_code == 409

    # The request was audited.
    events = audit.list_events("tenant-a", limit=100)
    assert any(e.event_type == "restore.requested" for e in events)


def test_apply_status_flips_to_applying(tmp_path: Path) -> None:
    client, _, export_dir, _ = _make_client(tmp_path)
    staged_id = "validated-2"
    _stage_validated(export_dir, staged_id)
    client.post(
        f"/api/v1/settings/backup/restore/{staged_id}/apply", json={"confirm": True}, headers=AUTH
    )
    status = client.get("/api/v1/settings/backup/restore/status", headers=AUTH).json()
    assert status["state"] == "applying"


# --------------------------------------------------------------------------------------------------
# Status + maintenance gate
# --------------------------------------------------------------------------------------------------


def test_status_idle_by_default(tmp_path: Path) -> None:
    client, _, _, _ = _make_client(tmp_path)
    status = client.get("/api/v1/settings/backup/restore/status", headers=AUTH).json()
    assert status["state"] == "idle"


def test_maintenance_flag_parks_mutating_requests(tmp_path: Path) -> None:
    client, _, _, backup_dir = _make_client(tmp_path)
    flag = backup_dir / "status" / "maintenance.flag"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("2026-06-26T00:00:00Z", encoding="utf-8")
    # A mutating request (POST) is parked with 503 while maintenance is on...
    resp = client.post(
        "/api/v1/settings/backup/restore/x/apply", json={"confirm": True}, headers=AUTH
    )
    assert resp.status_code == 503
    # ...but read-only GETs (including the restore status poll) still work.
    assert client.get("/api/v1/settings/backup/restore/status", headers=AUTH).status_code == 200


def test_preview_exempt_from_body_size_limit(tmp_path: Path) -> None:
    """A large body on the preview route is NOT rejected by the global body-size middleware (it is
    capped by DOKTOK_MAX_RESTORE_GB inside the route instead). A multi-MB body that would normally
    trip the default max_request_mb=25 passes the global gate here and is handled by the route."""
    client, _, _, _ = _make_client(tmp_path)
    big_body = b"x" * (30 * 1024 * 1024)  # 30 MB > default max_request_mb (25) for normal routes
    resp = client.post(
        "/api/v1/settings/backup/restore/preview",
        files={"file": ("a.enc", big_body, "application/octet-stream")},
        data={"passphrase": "a-long-enough-passphrase"},
        headers=AUTH,
    )
    # Not a 413 from the global middleware; the garbage decrypts-fail to ok=false at the route.
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_preview_validation_runs_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # F-11: the multi-GB decrypt/extract/hash used to run synchronously in the async handler,
    # stalling the uvicorn event loop for every request (including /health). It must be
    # dispatched to a worker thread via fastapi's run_in_threadpool.
    from doktok_api.routers import settings as settings_router
    from doktok_core.backup import restore as restore_mod_fn

    dispatched: list[object] = []

    async def _spy(fn: object, *args: object, **kwargs: object) -> object:
        dispatched.append(fn)
        return fn(*args, **kwargs)  # type: ignore[operator]

    monkeypatch.setattr(settings_router, "run_in_threadpool", _spy)
    client, _, _, _ = _make_client(tmp_path)
    payload = _build_encrypted_archive(tmp_path, monkeypatch, "preview-pass-1234")
    resp = client.post(
        "/api/v1/settings/backup/restore/preview",
        files={"file": ("backup.tgz.enc", payload, "application/octet-stream")},
        data={"passphrase": "preview-pass-1234"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert restore_mod_fn.validate_staged_upload in dispatched


# ---------------------------------------------------------------------------
# F-25 (#630): restore preview single-flight
# ---------------------------------------------------------------------------


def test_preview_claim_is_create_exclusive(tmp_path: Path) -> None:
    # F-25: one staged validation at a time - a concurrent preview is refused instantly
    # (create-exclusive), and releasing the slot lets the next preview in.
    export_dir = tmp_path / "exports"
    assert restore_mod.claim_preview(export_dir) is True
    assert restore_mod.claim_preview(export_dir) is False
    restore_mod.release_preview(export_dir)
    assert restore_mod.claim_preview(export_dir) is True


def test_stale_preview_claim_is_swept(tmp_path: Path) -> None:
    # F-25: a lock older than the staging TTL means the owning process died mid-preview - swept
    # at claim time so the route can never wedge.
    export_dir = tmp_path / "exports"
    assert restore_mod.claim_preview(export_dir) is True
    lock = export_dir / restore_mod._PREVIEW_LOCK  # noqa: SLF001
    stale = lock.stat().st_mtime - 7 * 3600
    os.utime(lock, (stale, stale))
    assert restore_mod.claim_preview(export_dir) is True


def test_second_preview_is_429_while_one_is_staged(tmp_path: Path) -> None:
    # With a preview in flight (the claim is held), the next preview gets 429.
    client, _, export_dir, _ = _make_client(tmp_path)
    assert restore_mod.claim_preview(export_dir) is True
    resp = client.post(
        "/api/v1/settings/backup/restore/preview",
        files={"file": ("a.enc", b"ciphertext", "application/octet-stream")},
        data={"passphrase": "a-long-enough-passphrase"},
        headers=AUTH,
    )
    assert resp.status_code == 429
    restore_mod.release_preview(export_dir)
