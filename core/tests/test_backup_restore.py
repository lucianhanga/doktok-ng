"""Unit tests for the portable backup RESTORE core (M12 portable restore, Phase 2).

Covers the two security-critical gates:
  * the independent safe-extraction validator (rejects absolute/``..``/symlink/hardlink/device
    members and oversized/too-many-entries/high-ratio archives; accepts a clean archive), and
  * manifest validation (checksum/HMAC mismatch refused; pg-major / newer-schema incompatibility
    refused; secrets-key mismatch -> warning, not error).

A real archive is built with the Phase-1 builder (pg_dump mocked) and encrypted with the real
openssl on the box, so the full decrypt -> safe-extract -> validate path is exercised end-to-end.
openssl-dependent tests self-skip when openssl is absent.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from doktok_contracts.schemas import BackupManifest, BackupManifestMember
from doktok_core.backup import export as export_mod
from doktok_core.backup import restore as restore_mod
from doktok_core.backup.export import ExportPaths
from doktok_core.backup.restore import (
    UnsafeArchiveError,
    assert_member_safe,
    check_compatibility,
    safe_extract,
    validate_manifest_integrity,
)
from doktok_core.backup.schema import schema_version_from_migrations

_FAKE_DUMP = b"PGDMP-fake-custom-format-dump\x00\x01\x02"
_SECRET = "unit-secret-key"  # pragma: allowlist secret
_REAL_RUN = subprocess.run


def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """pg_dump -> fake dump file; psql -> version; everything else (openssl) runs for real."""
    argv = args[0] if args else kwargs.get("args")
    assert isinstance(argv, list)
    if argv and argv[0] == "pg_dump":
        Path(argv[argv.index("-f") + 1]).write_bytes(_FAKE_DUMP)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    if argv and argv[0] == "psql":
        return subprocess.CompletedProcess(argv, 0, stdout="17.2\n", stderr="")
    return _REAL_RUN(*args, **kwargs)


# --------------------------------------------------------------------------------------------------
# Safe-extraction validator (the real gate) - crafted hostile tars, no encryption needed
# --------------------------------------------------------------------------------------------------


def _tinfo(name: str, *, typeflag: bytes = tarfile.REGTYPE, linkname: str = "") -> tarfile.TarInfo:
    ti = tarfile.TarInfo(name=name)
    ti.type = typeflag
    ti.linkname = linkname
    ti.size = 0
    return ti


def test_assert_member_safe_rejects_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(UnsafeArchiveError, match="absolute"):
        assert_member_safe(_tinfo("/etc/passwd"), tmp_path)


def test_assert_member_safe_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(UnsafeArchiveError, match="traversal"):
        assert_member_safe(_tinfo("files/../../escape.txt"), tmp_path)


def test_assert_member_safe_rejects_symlink(tmp_path: Path) -> None:
    with pytest.raises(UnsafeArchiveError, match="link"):
        assert_member_safe(
            _tinfo("evil", typeflag=tarfile.SYMTYPE, linkname="/etc/passwd"), tmp_path
        )


def test_assert_member_safe_rejects_hardlink(tmp_path: Path) -> None:
    with pytest.raises(UnsafeArchiveError, match="link"):
        assert_member_safe(_tinfo("evil", typeflag=tarfile.LNKTYPE, linkname="db.dump"), tmp_path)


def test_assert_member_safe_rejects_device(tmp_path: Path) -> None:
    with pytest.raises(UnsafeArchiveError, match="device|special"):
        assert_member_safe(_tinfo("dev", typeflag=tarfile.CHRTYPE), tmp_path)


def test_assert_member_safe_accepts_clean_file_and_dir(tmp_path: Path) -> None:
    assert_member_safe(_tinfo("files/ok.txt"), tmp_path)
    assert_member_safe(_tinfo("files/sub", typeflag=tarfile.DIRTYPE), tmp_path)


def _write_tar(path: Path, members: list[tuple[tarfile.TarInfo, bytes]]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for ti, data in members:
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))


def test_safe_extract_rejects_traversal_archive(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tgz"
    _write_tar(archive, [(_tinfo("../escape.txt"), b"x")])
    with pytest.raises(UnsafeArchiveError):
        safe_extract(archive, tmp_path / "out")


def test_safe_extract_rejects_too_many_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restore_mod, "MAX_ENTRIES", 2)
    archive = tmp_path / "many.tgz"
    _write_tar(archive, [(_tinfo(f"files/f{i}.txt"), b"x") for i in range(5)])
    with pytest.raises(UnsafeArchiveError, match="too many entries"):
        safe_extract(archive, tmp_path / "out")


def test_safe_extract_rejects_oversized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(restore_mod, "MAX_TOTAL_UNCOMPRESSED", 4)
    archive = tmp_path / "big.tgz"
    _write_tar(archive, [(_tinfo("files/f.txt"), b"way more than four bytes")])
    with pytest.raises(UnsafeArchiveError, match="uncompressed size"):
        safe_extract(archive, tmp_path / "out")


def test_safe_extract_rejects_decompression_bomb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restore_mod, "MAX_COMPRESSION_RATIO", 2)
    archive = tmp_path / "bomb.tgz"
    # 50 MiB of zeros gzips to tens of KB (> the 4096-byte tiny-archive floor), so the ratio guard
    # is exercised and the expanded:compressed ratio (~1000) blows past the patched cap of 2.
    _write_tar(archive, [(_tinfo("files/zeros.bin"), b"\x00" * (50 * 1024 * 1024))])
    with pytest.raises(UnsafeArchiveError, match="compression ratio"):
        safe_extract(archive, tmp_path / "out")


def test_safe_extract_accepts_clean_archive(tmp_path: Path) -> None:
    archive = tmp_path / "clean.tgz"
    _write_tar(
        archive,
        [
            (_tinfo("db.dump"), b"dump-bytes"),
            (_tinfo("files/a.txt"), b"hello"),
            (_tinfo("files/sub/b.txt"), b"world"),
        ],
    )
    out = tmp_path / "out"
    count, total = safe_extract(archive, out)
    assert count == 3
    assert total == len(b"dump-bytes") + len(b"hello") + len(b"world")
    assert (out / "files" / "a.txt").read_text() == "hello"


# --------------------------------------------------------------------------------------------------
# Manifest integrity + version compatibility
# --------------------------------------------------------------------------------------------------


def _manifest(
    members: list[BackupManifestMember], *, pg: str = "17.2", schema: int = 5
) -> BackupManifest:
    m = BackupManifest(
        created_at=datetime.now(UTC),
        app_version="0.2.0",
        pg_version=pg,
        app_schema_version=schema,
        members=members,
    )
    m.manifest_hmac = export_mod._manifest_hmac(_SECRET, members)
    return m


def test_manifest_integrity_ok() -> None:
    members = [BackupManifestMember(name="db.dump", size=3, sha256="abc")]
    assert validate_manifest_integrity(_manifest(members), _SECRET) == []


def test_manifest_integrity_detects_tampered_hmac() -> None:
    members = [BackupManifestMember(name="db.dump", size=3, sha256="abc")]
    m = _manifest(members)
    m.manifest_hmac = "deadbeef"
    errors = validate_manifest_integrity(m, _SECRET)
    assert errors and "integrity" in errors[0]


def test_manifest_integrity_missing_tag_refused() -> None:
    members = [BackupManifestMember(name="db.dump", size=3, sha256="abc")]
    m = _manifest(members)
    m.manifest_hmac = ""
    assert validate_manifest_integrity(m, _SECRET) != []


def test_compatibility_pg_major_mismatch_refused() -> None:
    compatible, errors, _ = check_compatibility(_manifest([], pg="16.4"), running_schema_version=10)
    assert not compatible and any("Postgres major" in e for e in errors)


def test_compatibility_newer_schema_refused() -> None:
    compatible, errors, _ = check_compatibility(_manifest([], schema=20), running_schema_version=10)
    assert not compatible and any("NEWER" in e for e in errors)


def test_compatibility_older_or_equal_schema_ok() -> None:
    compatible, errors, _ = check_compatibility(_manifest([], schema=8), running_schema_version=10)
    assert compatible and errors == []


def test_compatibility_unknown_archive_schema_ok() -> None:
    # A Phase-1 archive (schema 0) is treated as compatible.
    compatible, errors, _ = check_compatibility(_manifest([], schema=0), running_schema_version=10)
    assert compatible and errors == []


def test_schema_version_from_migrations(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("x")
    (tmp_path / "0031_b.sql").write_text("x")
    (tmp_path / "notes.txt").write_text("ignored")
    assert schema_version_from_migrations(tmp_path) == 31
    assert schema_version_from_migrations(tmp_path / "missing") == 0


# --------------------------------------------------------------------------------------------------
# Full decrypt -> safe-extract -> validate path against a REAL built+encrypted archive
# --------------------------------------------------------------------------------------------------


def _build_and_encrypt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, passphrase: str, *, secret: str = _SECRET
) -> tuple[Path, Path]:
    """Build a real plaintext archive (pg_dump mocked) and openssl-encrypt it. Returns (export_dir,
    enc_path)."""
    monkeypatch.setattr(export_mod.subprocess, "run", _fake_run)
    export_dir = tmp_path / "exports"
    files_root = tmp_path / "files"
    (files_root / "sub").mkdir(parents=True)
    (files_root / "doc.txt").write_text("payload", encoding="utf-8")
    (files_root / "sub" / "note.txt").write_text("a note", encoding="utf-8")
    paths = ExportPaths(
        export_dir=export_dir,
        files_root=files_root,
        database_url="postgresql://doktok:doktok@db:5432/doktok",  # pragma: allowlist secret
        secrets_key=secret,
        app_version="0.2.0",
        app_schema_version=5,
    )
    info = export_mod.build_export(paths, "exp1")
    assert info.status == "ready"
    staged = export_mod.staged_archive_path(export_dir, "exp1")
    enc = tmp_path / "upload.enc"
    proc = _REAL_RUN(
        export_mod.encrypt_argv(staged, enc),
        input=(passphrase + "\n").encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return export_dir, enc


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_validate_staged_upload_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    passphrase = "correct horse battery staple"  # pragma: allowlist secret
    export_dir, enc = _build_and_encrypt(tmp_path, monkeypatch, passphrase)
    staged_id = "stg1"
    upload = restore_mod.upload_path(export_dir, staged_id)
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(enc, upload)

    result = restore_mod.validate_staged_upload(
        export_dir,
        staged_id,
        passphrase,
        secrets_key=_SECRET,
        running_schema_version=10,
        actor="tester",
    )
    assert result.ok is True
    assert result.compatible is True
    assert result.errors == []
    assert result.secrets_key_match is True
    assert result.member_count == 4  # db.dump + manifest.json + doc.txt + sub/note.txt
    assert result.pg_version == "17.2"
    assert restore_mod.is_validated(export_dir, staged_id) is True
    # F-34 (#646): the validated marker records WHO previewed - the apply binds to that operator.
    assert restore_mod.validated_actor(export_dir, staged_id) == "tester"


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_validate_staged_upload_wrong_passphrase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export_dir, enc = _build_and_encrypt(tmp_path, monkeypatch, "the right passphrase")
    staged_id = "stg2"
    upload = restore_mod.upload_path(export_dir, staged_id)
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(enc, upload)

    result = restore_mod.validate_staged_upload(
        export_dir,
        staged_id,
        "the WRONG passphrase",
        secrets_key=_SECRET,
        running_schema_version=10,
        actor="tester",
    )
    assert result.ok is False
    assert any("decrypt" in e for e in result.errors)
    assert restore_mod.is_validated(export_dir, staged_id) is False


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_validate_staged_upload_secrets_key_mismatch_is_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    passphrase = "another good passphrase here"  # pragma: allowlist secret
    # Archive produced with a DIFFERENT secrets key than the validating box.
    export_dir, enc = _build_and_encrypt(tmp_path, monkeypatch, passphrase, secret="producer-key")
    staged_id = "stg3"
    upload = restore_mod.upload_path(export_dir, staged_id)
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(enc, upload)

    result = restore_mod.validate_staged_upload(
        export_dir,
        staged_id,
        passphrase,
        secrets_key="this-box-key",
        running_schema_version=10,
        actor="tester",
    )
    # The keyed HMAC won't reproduce with a different key, AND the mismatch is surfaced as a warning
    # + secrets_key_match=False (the key-warning path is exercised).
    assert result.secrets_key_match is False
    assert any("secrets key" in w for w in result.warnings)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_validate_staged_upload_tampered_member_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member whose bytes were swapped after the manifest was written must be refused."""
    passphrase = "tamper test passphrase!!"  # pragma: allowlist secret
    monkeypatch.setattr(export_mod.subprocess, "run", _fake_run)
    export_dir = tmp_path / "exports"
    files_root = tmp_path / "files"
    files_root.mkdir(parents=True)
    (files_root / "doc.txt").write_text("original", encoding="utf-8")
    paths = ExportPaths(
        export_dir=export_dir,
        files_root=files_root,
        database_url="postgresql://doktok:doktok@db:5432/doktok",  # pragma: allowlist secret
        secrets_key=_SECRET,
        app_version="0.2.0",
        app_schema_version=5,
    )
    export_mod.build_export(paths, "exp1")
    staged = export_mod.staged_archive_path(export_dir, "exp1")

    # Rebuild the tar with one member's bytes corrupted but the manifest left untouched.
    tampered = tmp_path / "tampered.tgz"
    with tarfile.open(staged, "r:gz") as src, tarfile.open(tampered, "w:gz") as dst:
        for member in src.getmembers():
            data = src.extractfile(member)
            payload = data.read() if data is not None else b""
            if member.name == "files/doc.txt":
                payload = b"TAMPERED-BYTES-DIFFERENT-LEN"
                member.size = len(payload)
            dst.addfile(member, io.BytesIO(payload))

    enc = tmp_path / "upload.enc"
    proc = _REAL_RUN(
        export_mod.encrypt_argv(tampered, enc),
        input=(passphrase + "\n").encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    staged_id = "stg4"
    upload = restore_mod.upload_path(export_dir, staged_id)
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(enc, upload)
    result = restore_mod.validate_staged_upload(
        export_dir,
        staged_id,
        passphrase,
        secrets_key=_SECRET,
        running_schema_version=10,
        actor="tester",
    )
    assert result.ok is False
    assert any("mismatch" in e for e in result.errors)


def test_restore_status_roundtrip(tmp_path: Path) -> None:
    status_dir = tmp_path / "status"
    assert restore_mod.read_restore_status(status_dir)["state"] == "idle"
    restore_mod.write_restore_status(
        status_dir, state="applying", step="db", detail="restoring", restore_id="r1"
    )
    raw = restore_mod.read_restore_status(status_dir)
    assert raw["state"] == "applying" and raw["step"] == "db" and raw["restore_id"] == "r1"
    # An invalid state in the file is normalized back to idle (defensive).
    (status_dir / "restore.json").write_text(json.dumps({"state": "bogus"}), encoding="utf-8")
    assert restore_mod.read_restore_status(status_dir)["state"] == "idle"


# ---------------------------------------------------------------------------
# F-17 (#632): OWASP-current PBKDF2 work factor + legacy-count fallback
# ---------------------------------------------------------------------------


def test_encryption_uses_600k_pbkdf2_iterations() -> None:
    # F-17: the archive encryption pins the OWASP-current work factor (was the 10k openssl
    # default) and an explicit digest, so human passphrases cost ~60x more to crack offline.
    argv = export_mod.encrypt_argv(Path("a.tgz"), Path("a.tgz.enc"))
    assert "-iter" in argv and argv[argv.index("-iter") + 1] == "600000"
    assert "-md" in argv and argv[argv.index("-md") + 1] == "sha256"


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_new_archive_round_trips_with_the_new_work_factor(tmp_path: Path) -> None:
    # A 600k-encrypted archive decrypts through the restore path (first attempt, no fallback).
    import subprocess as sp

    plain = tmp_path / "plain.tgz"
    plain.write_bytes(b"archive-bytes-600k")
    enc = tmp_path / "new.tgz.enc"
    proc = sp.run(
        export_mod.encrypt_argv(plain, enc),
        input=b"new-pass-1234\n",
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = tmp_path / "out.tgz"
    restore_mod.decrypt_archive(enc, out, "new-pass-1234")
    assert out.read_bytes() == b"archive-bytes-600k"


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_pre_change_archive_still_decrypts_via_fallback(tmp_path: Path) -> None:
    # F-17: archives written with the openssl DEFAULT (10k) count before this change still
    # decrypt - the restore path falls back to the legacy count when 600k fails.
    import subprocess as sp

    plain = tmp_path / "plain.tgz"
    plain.write_bytes(b"archive-bytes-10k")
    enc = tmp_path / "old.tgz.enc"
    proc = sp.run(
        [
            "openssl",
            "enc",
            "-aes-256-cbc",
            "-pbkdf2",
            "-salt",
            "-pass",
            "stdin",
            "-in",
            str(plain),
            "-out",
            str(enc),
        ],
        input=b"old-pass-1234\n",
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = tmp_path / "out.tgz"
    restore_mod.decrypt_archive(enc, out, "old-pass-1234")
    assert out.read_bytes() == b"archive-bytes-10k"


# ---------------------------------------------------------------------------
# F-28 (#640): manifest member names are untrusted input - no filesystem probes
# ---------------------------------------------------------------------------


def test_verify_members_rejects_hostile_names(tmp_path: Path) -> None:
    # F-28: an absolute name or a '..' escape must be refused as UNSAFE, never joined onto the
    # extraction root and probed against the host filesystem (existence oracle).
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    members = [
        BackupManifestMember(name="/etc/hosts", sha256="0" * 64, size=1),
        BackupManifestMember(name="../../etc/hosts", sha256="0" * 64, size=1),
        BackupManifestMember(name="C:\\Windows\\win.ini", sha256="0" * 64, size=1),
    ]
    errors = restore_mod.verify_members(extracted, members)
    assert len(errors) == 3
    assert all("unsafe member name" in e for e in errors)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_hmac_failure_skips_member_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # F-28: an HMAC-unauthenticated manifest is attacker-authored - member verification is
    # skipped entirely, so its names are never probed against the host filesystem.
    passphrase = "hmac skip test passphrase"  # pragma: allowlist secret
    monkeypatch.setattr(export_mod.subprocess, "run", _fake_run)
    export_dir = tmp_path / "exports"
    files_root = tmp_path / "files"
    files_root.mkdir(parents=True)
    (files_root / "doc.txt").write_text("payload", encoding="utf-8")
    paths = ExportPaths(
        export_dir=export_dir,
        files_root=files_root,
        database_url="postgresql://doktok:doktok@db:5432/doktok",  # pragma: allowlist secret
        secrets_key=_SECRET,
        app_version="0.2.0",
        app_schema_version=5,
    )
    export_mod.build_export(paths, "exp1")
    staged = export_mod.staged_archive_path(export_dir, "exp1")

    # Repack with a forged member list pointing at a host file; the manifest HMAC (over the
    # original member list) no longer matches, so the manifest is unauthenticated.
    forged = tmp_path / "forged.tgz"
    with tarfile.open(staged, "r:gz") as src, tarfile.open(forged, "w:gz") as dst:
        for member in src.getmembers():
            data = src.extractfile(member)
            payload = data.read() if data is not None else b""
            if member.name == "manifest.json":
                forged_manifest = json.loads(payload)
                forged_manifest["members"] = [{"name": "/etc/hosts", "sha256": "0" * 64, "size": 1}]
                payload = json.dumps(forged_manifest).encode()
                member.size = len(payload)
            dst.addfile(member, io.BytesIO(payload))

    enc = tmp_path / "upload.enc"
    proc = _REAL_RUN(
        export_mod.encrypt_argv(forged, enc),
        input=(passphrase + "\n").encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    staged_id = "stg-f28"
    upload = restore_mod.upload_path(export_dir, staged_id)
    upload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(enc, upload)
    result = restore_mod.validate_staged_upload(
        export_dir,
        staged_id,
        passphrase,
        secrets_key=_SECRET,
        running_schema_version=10,
        actor="tester",
    )
    assert result.ok is False
    assert any("integrity" in e for e in result.errors)
    # No member-level probe results leak: the forged name appears NOWHERE in the errors.
    assert not any("/etc/hosts" in e for e in result.errors)
