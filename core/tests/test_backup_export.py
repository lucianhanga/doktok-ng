"""Export-build tests for the portable one-file backup (M12 portable backup, Phase 1).

pg_dump is mocked (we write a fake custom-format dump file) so the test runs without a database. The
files_root tree and the manifest are real, so we verify the true archive layout + checksums."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

import pytest
from doktok_contracts.schemas import BackupManifest
from doktok_core.backup import export as export_mod
from doktok_core.backup.export import ExportPaths
from doktok_core.backup.fingerprint import secrets_key_fingerprint

_FAKE_DUMP = b"PGDMP-fake-custom-format-dump\x00\x01\x02"


def _fake_subprocess_run(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
    """Stand in for pg_dump (writes a fake dump to the -f target) and psql (server_version)."""
    argv = args[0] if args else kwargs.get("args")
    assert isinstance(argv, list)
    if argv and argv[0] == "pg_dump":
        out_path = Path(argv[argv.index("-f") + 1])
        out_path.write_bytes(_FAKE_DUMP)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    if argv and argv[0] == "psql":
        return subprocess.CompletedProcess(argv, 0, stdout="17.2\n", stderr="")
    return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


@pytest.fixture
def _paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ExportPaths:
    monkeypatch.setattr(export_mod.subprocess, "run", _fake_subprocess_run)
    files_root = tmp_path / "files"
    (files_root / "sub").mkdir(parents=True)
    (files_root / "invoice.pdf").write_bytes(b"hello pdf bytes")
    (files_root / "sub" / "note.txt").write_text("a note", encoding="utf-8")
    return ExportPaths(
        export_dir=tmp_path / "exports",
        files_root=files_root,
        database_url="postgresql://doktok:doktok@db:5432/doktok",  # pragma: allowlist secret
        secrets_key="test-secret-key",  # pragma: allowlist secret
        app_version="0.2.0",
    )


def _extract_manifest(archive: Path) -> BackupManifest:
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.extractfile("manifest.json")
        assert member is not None
        return BackupManifest.model_validate(json.loads(member.read().decode("utf-8")))


def test_build_produces_archive_with_db_files_and_manifest(_paths: ExportPaths) -> None:
    info = export_mod.build_export(_paths, "exp1")
    assert info.status == "ready"
    archive = export_mod.staged_archive_path(_paths.export_dir, "exp1")
    assert archive.exists()
    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
    assert "db.dump" in names
    assert "manifest.json" in names
    assert "files/invoice.pdf" in names
    assert "files/sub/note.txt" in names


def test_manifest_checksums_match_archived_bytes(_paths: ExportPaths) -> None:
    export_mod.build_export(_paths, "exp2")
    archive = export_mod.staged_archive_path(_paths.export_dir, "exp2")
    manifest = _extract_manifest(archive)
    by_name = {m.name: m for m in manifest.members}
    # db.dump checksum matches the fake dump bytes.
    assert by_name["db.dump"].sha256 == hashlib.sha256(_FAKE_DUMP).hexdigest()
    assert by_name["db.dump"].size == len(_FAKE_DUMP)
    # Each archived file member's checksum matches the bytes inside the archive.
    with tarfile.open(archive, "r:gz") as tar:
        for name in ("files/invoice.pdf", "files/sub/note.txt"):
            fh = tar.extractfile(name)
            assert fh is not None
            data = fh.read()
            assert by_name[name].sha256 == hashlib.sha256(data).hexdigest()
            assert by_name[name].size == len(data)


def test_manifest_is_data_only_no_secrets(_paths: ExportPaths) -> None:
    export_mod.build_export(_paths, "exp3")
    archive = export_mod.staged_archive_path(_paths.export_dir, "exp3")
    raw = archive.read_bytes()
    # The plaintext secrets key, the DSN, and password must never appear in the archive bytes.
    assert b"test-secret-key" not in raw
    assert b"postgresql://" not in raw
    assert b"doktok:doktok" not in raw
    manifest = _extract_manifest(archive)
    # The fingerprint is present but is the non-reversible HMAC, not the key.
    assert manifest.secrets_key_fingerprint == secrets_key_fingerprint("test-secret-key")
    assert manifest.secrets_key_fingerprint != "test-secret-key"
    # No secret VALUE (the key, the DSN, the password) appears anywhere in the manifest. (The field
    # NAME secrets_key_fingerprint legitimately contains "secrets_key" - that is not a leak.)
    blob = manifest.model_dump_json()
    for forbidden in ("test-secret-key", "postgresql://", "doktok:doktok"):
        assert forbidden not in blob


def test_fingerprint_is_stable_and_empty_without_key() -> None:
    assert secrets_key_fingerprint("") == ""
    fp1 = secrets_key_fingerprint("abc")
    fp2 = secrets_key_fingerprint("abc")
    assert fp1 == fp2 and fp1 != ""
    assert secrets_key_fingerprint("abc") != secrets_key_fingerprint("abd")


def test_status_transitions_building_to_ready(_paths: ExportPaths) -> None:
    # No status before the build.
    assert export_mod.read_export_status(_paths.export_dir, "exp4") is None
    info = export_mod.build_export(_paths, "exp4")
    assert info.status == "ready"
    persisted = export_mod.read_export_status(_paths.export_dir, "exp4")
    assert persisted is not None and persisted.status == "ready"
    assert persisted.member_count == 3  # db.dump + 2 files
    assert persisted.pg_version == "17.2"


def test_build_failure_records_failed_status(
    _paths: ExportPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _failing_run(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        argv = args[0] if args else kwargs.get("args")
        assert isinstance(argv, list)
        if argv and argv[0] == "pg_dump":
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="connection refused")
        return subprocess.CompletedProcess(argv, 0, stdout="17.2\n", stderr="")

    monkeypatch.setattr(export_mod.subprocess, "run", _failing_run)
    info = export_mod.build_export(_paths, "exp5")
    assert info.status == "failed"
    assert info.error  # short non-secret message
    assert "://" not in info.error
    # The staged archive must not linger on failure.
    assert not export_mod.staged_archive_path(_paths.export_dir, "exp5").exists()


def test_staged_archive_is_0600(_paths: ExportPaths) -> None:
    export_mod.build_export(_paths, "exp6")
    archive = export_mod.staged_archive_path(_paths.export_dir, "exp6")
    assert (archive.stat().st_mode & 0o777) == 0o600


def test_single_flight_detects_in_progress_build(_paths: ExportPaths) -> None:
    assert export_mod.is_build_in_progress(_paths.export_dir) is False
    # Simulate a build that is still running by writing a 'building' status.
    export_mod._write_status(
        _paths.export_dir,
        export_mod.BackupExportInfo(export_id="busy", status="building"),
    )
    assert export_mod.is_build_in_progress(_paths.export_dir) is True


def test_sweep_removes_stale_archives(_paths: ExportPaths) -> None:
    export_mod.build_export(_paths, "exp7")
    archive = export_mod.staged_archive_path(_paths.export_dir, "exp7")
    assert archive.exists()
    # ttl=0 => everything is stale.
    removed = export_mod.sweep_stale_exports(_paths.export_dir, ttl_seconds=0)
    assert removed >= 1
    assert not archive.exists()
    assert export_mod.read_export_status(_paths.export_dir, "exp7") is None


def test_encrypt_argv_is_aes256_pbkdf2_stdin() -> None:
    argv = export_mod.encrypt_argv(Path("/in.tgz"), Path("/out.tgz.enc"))
    assert argv[:2] == ["openssl", "enc"]
    assert "-aes-256-cbc" in argv
    assert "-pbkdf2" in argv
    assert "-salt" in argv
    assert "stdin" in argv  # passphrase via stdin, never the command line
    assert argv[argv.index("-in") + 1] == "/in.tgz"
    assert argv[argv.index("-out") + 1] == "/out.tgz.enc"
