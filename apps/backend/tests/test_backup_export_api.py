"""Endpoint tests for the portable one-file backup export (M12 portable backup, Phase 1).

pg_dump/psql are mocked so the build runs without a database; the encrypted download uses the real
openssl on the box (the test is skipped if openssl is unavailable). Covers: auth, async build +
single-flight/rate-limit 429s, status transitions, an openssl-decryptable download with the right
passphrase (and failure with the wrong one), and that the passphrase never reaches the logs."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository, AuditLogRepository
from doktok_contracts.schemas import BackupExportInfo
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.backup import export as export_mod
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}
AUTH = {"Authorization": "Bearer tok-a"}
_FAKE_DUMP = b"PGDMP-fake-custom-format-dump\x00\x01\x02"

# The real subprocess.run, captured before any patching, so the openssl encryption at the download
# boundary runs for real even while pg_dump/psql are stubbed (export_mod.subprocess is the global
# subprocess module, shared with the router - patching .run affects openssl too unless we delegate).
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
    # Everything else (notably openssl at the download boundary) runs for real.
    return _REAL_RUN(*args, **kwargs)


def _make_client(tmp_path: Path) -> tuple[TestClient, InMemoryAuditLogRepository, Path]:
    export_dir = tmp_path / "exports"
    client, audit = _make_client_with_export_dir(tmp_path, export_dir)
    return client, audit, export_dir


def _make_client_with_export_dir(
    tmp_path: Path, export_dir: Path
) -> tuple[TestClient, InMemoryAuditLogRepository]:
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
        backup_export_dir=str(export_dir),
        secrets_key="unit-secret",  # pragma: allowlist secret
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry)), audit


def test_requires_auth(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    assert client.post("/api/v1/settings/backup/export").status_code == 401
    assert client.get("/api/v1/settings/backup/export/status").status_code == 401
    assert client.post("/api/v1/settings/backup/export/x/download").status_code == 401


def test_start_export_runs_build_to_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_mod.subprocess, "run", _fake_run)
    client, _, export_dir = _make_client(tmp_path)
    resp = client.post("/api/v1/settings/backup/export", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    export_id = body["export_id"]
    # TestClient runs the BackgroundTask synchronously after the response, so it is ready now.
    status = client.get(
        "/api/v1/settings/backup/export/status",
        params={"export_id": export_id},
        headers=AUTH,
    ).json()
    assert status["status"] == "ready"
    assert status["member_count"] == 2  # db.dump + doc.txt
    assert status["pg_version"] == "17.2"
    assert export_mod.staged_archive_path(export_dir, export_id).exists()


def test_single_flight_returns_429(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_mod.subprocess, "run", _fake_run)
    client, _, export_dir = _make_client(tmp_path)
    # Simulate a build already in progress.
    export_mod._write_status(
        export_dir,
        export_mod.BackupExportInfo(export_id="busy", status="building"),
    )
    resp = client.post("/api/v1/settings/backup/export", headers=AUTH)
    assert resp.status_code == 429


def test_rate_limit_returns_429(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_mod.subprocess, "run", _fake_run)
    client, _, export_dir = _make_client(tmp_path)
    export_mod._write_status(
        export_dir,
        export_mod.BackupExportInfo(
            export_id="recent", status="ready", created_at=datetime.now(UTC)
        ),
    )
    resp = client.post("/api/v1/settings/backup/export", headers=AUTH)
    assert resp.status_code == 429


def test_status_404_when_unknown(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    resp = client.get(
        "/api/v1/settings/backup/export/status",
        params={"export_id": "nope"},
        headers=AUTH,
    )
    assert resp.status_code == 404


def test_download_404_when_not_ready(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/settings/backup/export/missing/download",
        json={"passphrase": "averylongpassphrase"},
        headers=AUTH,
    )
    assert resp.status_code == 404


def test_download_short_passphrase_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export_mod.subprocess, "run", _fake_run)
    client, _, export_dir = _make_client(tmp_path)
    body = client.post("/api/v1/settings/backup/export", headers=AUTH).json()
    resp = client.post(
        f"/api/v1/settings/backup/export/{body['export_id']}/download",
        json={"passphrase": "short"},  # < 8 chars
        headers=AUTH,
    )
    assert resp.status_code == 422


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_download_is_openssl_decryptable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_mod.subprocess, "run", _fake_run)
    client, _, export_dir = _make_client(tmp_path)
    export_id = client.post("/api/v1/settings/backup/export", headers=AUTH).json()["export_id"]

    passphrase = "correct horse battery staple"  # pragma: allowlist secret
    resp = client.post(
        f"/api/v1/settings/backup/export/{export_id}/download",
        json={"passphrase": passphrase},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["content-disposition"].endswith('.tgz.enc"')
    ciphertext = resp.content
    assert ciphertext  # non-empty encrypted blob

    enc_file = tmp_path / "out.tgz.enc"
    enc_file.write_bytes(ciphertext)
    dec_file = tmp_path / "out.tgz"

    # Right passphrase decrypts to a valid gzip tar carrying the manifest + db.dump.
    proc = subprocess.run(
        [
            "openssl",
            "enc",
            "-d",
            "-aes-256-cbc",
            "-pbkdf2",
            "-iter",
            "600000",
            "-md",
            "sha256",
            "-salt",
            "-pass",
            "stdin",
            "-in",
            str(enc_file),
            "-out",
            str(dec_file),
        ],
        input=(passphrase + "\n").encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    with tarfile.open(dec_file, "r:gz") as tar:
        names = set(tar.getnames())
    assert {"db.dump", "manifest.json", "files/doc.txt"} <= names

    # Wrong passphrase fails to decrypt.
    bad = subprocess.run(
        [
            "openssl",
            "enc",
            "-d",
            "-aes-256-cbc",
            "-pbkdf2",
            "-salt",
            "-pass",
            "stdin",
            "-in",
            str(enc_file),
            "-out",
            str(tmp_path / "bad.tgz"),
        ],
        input=b"the wrong passphrase\n",
        capture_output=True,
        check=False,
    )
    assert bad.returncode != 0


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_passphrase_never_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(export_mod.subprocess, "run", _fake_run)
    client, _, _ = _make_client(tmp_path)
    export_id = client.post("/api/v1/settings/backup/export", headers=AUTH).json()["export_id"]
    secret = "super-secret-passphrase-value"  # pragma: allowlist secret
    with caplog.at_level(logging.DEBUG):
        resp = client.post(
            f"/api/v1/settings/backup/export/{export_id}/download",
            json={"passphrase": secret},
            headers=AUTH,
        )
    assert resp.status_code == 200
    assert secret not in caplog.text


def test_download_records_portable_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export_mod.subprocess, "run", _fake_run)
    client, audit, _ = _make_client(tmp_path)
    client.post("/api/v1/settings/backup/export", headers=AUTH)
    # The build start + success events are recorded into the activity log with the portable leg.
    events = audit.list_events("tenant-a", limit=100)
    legs = {e.metadata.get("leg") for e in events if isinstance(e.metadata, dict)}
    assert "portable" in legs


# ---------------------------------------------------------------------------
# F-24 (#629): atomic single-flight + stale-building recovery
# ---------------------------------------------------------------------------


def test_claim_build_is_create_exclusive(tmp_path: Path) -> None:
    # F-24: the build slot is claimed atomically (O_CREAT|O_EXCL) - a concurrent starter loses
    # INSTANTLY instead of racing into a parallel pg_dump.
    export_dir = tmp_path / "exports"
    first = export_mod.claim_build(export_dir, "id-a", "1.0")
    assert first is not None and first.status == "building"
    assert export_mod.claim_build(export_dir, "id-b", "1.0") is None


def test_start_export_is_429_when_a_build_is_in_flight(tmp_path: Path) -> None:
    # Pins the single-flight guard through the API: an in-flight build 429s the next starter.
    export_dir = tmp_path / "exports"
    export_mod.claim_build(export_dir, "in-flight", "1.0")
    client, _audit = _make_client_with_export_dir(tmp_path, export_dir)
    resp = client.post("/api/v1/settings/backup/export", headers=AUTH)
    assert resp.status_code == 429


def test_stale_building_status_is_swept_and_exports_work_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # F-24: a crashed build leaves a 'building' status behind; older than the TTL it is stale -
    # swept and treated as not-in-progress, so the feature can never wedge at 429 forever.
    export_dir = tmp_path / "exports"
    export_dir.mkdir(parents=True)
    stale = BackupExportInfo(
        export_id="crashed",
        status="building",
        created_at=datetime.now(UTC) - timedelta(hours=25),
        app_version="1.0",
    )
    export_dir.joinpath("crashed.status.json").write_text(stale.model_dump_json(), encoding="utf-8")
    assert export_mod.is_build_in_progress(export_dir) is False  # stale -> swept
    assert not export_dir.joinpath("crashed.status.json").exists()

    monkeypatch.setattr(export_mod.subprocess, "run", _fake_run)
    client, _audit = _make_client_with_export_dir(tmp_path, export_dir)
    resp = client.post("/api/v1/settings/backup/export", headers=AUTH)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# F-20 (#634): the staged plaintext archive is discarded after the download
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_download_discards_staged_plaintext_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # F-20: once the archive has been streamed, the staged PLAINTEXT .tgz (and its status file)
    # must not linger until the 24h TTL sweep - it is deleted when the response finishes.
    monkeypatch.setattr(export_mod.subprocess, "run", _fake_run)
    client, _, export_dir = _make_client(tmp_path)
    export_id = client.post("/api/v1/settings/backup/export", headers=AUTH).json()["export_id"]
    staged = export_mod.staged_archive_path(export_dir, export_id)
    assert staged.exists()

    resp = client.post(
        f"/api/v1/settings/backup/export/{export_id}/download",
        json={"passphrase": "correct horse battery staple"},  # pragma: allowlist secret
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert not staged.exists()
    assert not export_dir.joinpath(f"{export_id}.status.json").exists()

    # A second download of the discarded export is a clean 404, not a stale plaintext read.
    again = client.post(
        f"/api/v1/settings/backup/export/{export_id}/download",
        json={"passphrase": "correct horse battery staple"},  # pragma: allowlist secret
        headers=AUTH,
    )
    assert again.status_code == 404
