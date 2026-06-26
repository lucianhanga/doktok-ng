"""Portable backup RESTORE: decrypt + SAFE extraction + manifest/version validation (M12 Phase 2).

This is the NON-destructive half of restore and runs IN THE BACKEND (it has openssl + pg client
from Phase 1). It takes an uploaded, passphrase-encrypted ``.tgz.enc``, decrypts it, extracts it to
a staging dir behind a hostile-archive gate, and validates it. It NEVER mutates the live DB or the
files_root - the destructive apply is a separate root host helper (deploy/restore-import.sh) that
only ever runs against a pre-validated staging dir.

This is the most dangerous feature in the app, so the extractor assumes the archive is adversarial:

  * an INDEPENDENT pre-extraction validator is the real gate. It rejects absolute paths, any ``..``
    traversal, symlinks, hardlinks, device/fifo/special members, and any member whose resolved path
    escapes the staging root; it enforces a max entry count, a max total uncompressed size (a
    streamed byte counter), and a max compression ratio (a decompression-bomb guard).
  * Python ``tarfile`` extraction with ``filter="data"`` is used AS WELL (defense in depth), but the
    manual validator is the authority - we assume the stdlib filter can be bypassed.

After extraction the manifest is verified: every member's sha256 is recomputed and compared, the
manifest HMAC is recomputed and compared (tamper/corruption -> refuse), and version compatibility is
checked (pg major must be 17; the archive's app schema generation must not be NEWER than the running
code). The secrets-key fingerprint mismatch is a WARNING (the OpenAI key won't decrypt), not a hard
error. Everything is bounded-memory and cleans up its staging dir on any failure.

The package is stdlib + subprocess (openssl) + filesystem only, no infra adapter, so it stays in the
core layer (import-linter).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil

# subprocess: openssl is run with a fixed argv (no shell). Re-exported (redundant alias) so tests
# can monkeypatch ``restore.subprocess.run`` under strict mypy.
import subprocess as subprocess
import tarfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from doktok_contracts.schemas import BackupManifest, BackupManifestMember

from doktok_core.backup.export import _manifest_hmac
from doktok_core.backup.fingerprint import secrets_key_fingerprint

logger = logging.getLogger("doktok.backup.restore")

_CHUNK = 1024 * 1024  # 1 MiB streaming chunk
REQUIRED_PG_MAJOR = 17  # custom-format logical dumps are portable only within the same pg major

# Hostile-archive guards (defense against zip/tar bombs + resource exhaustion). Tunable but sane:
MAX_ENTRIES = 5_000_000  # refuse archives with absurdly many members
MAX_TOTAL_UNCOMPRESSED = 2 * 1024**4  # 2 TiB cap on total expanded bytes
MAX_COMPRESSION_RATIO = 200  # expanded:compressed beyond this is treated as a decompression bomb

DEFAULT_TTL_SECONDS = 6 * 3600  # validated staging dirs older than this are swept


@dataclass
class StagedValidation:
    """The outcome of decrypt+extract+validate for one staged archive (feeds RestorePreview).
    ``ok`` is True only when ``errors`` is empty AND ``compatible`` is True."""

    staged_id: str
    staging_dir: Path
    ok: bool = False
    compatible: bool = False
    app_version: str = ""
    pg_version: str = ""
    created_at: datetime | None = None
    member_count: int = 0
    total_bytes: int = 0
    secrets_key_match: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class UnsafeArchiveError(Exception):
    """A tar member or the archive as a whole violated a safe-extraction rule (hostile archive)."""


def restores_root(export_dir: Path) -> Path:
    """The writable root that holds per-restore staging dirs (sibling to the exports staging)."""
    return export_dir / "restores"


def staging_dir(export_dir: Path, staged_id: str) -> Path:
    """The staging dir for one restore attempt (holds upload.enc, the extracted tree, status)."""
    return restores_root(export_dir) / staged_id


def upload_path(export_dir: Path, staged_id: str) -> Path:
    """Where the streamed encrypted upload lands (0600), before decrypt+extract."""
    return staging_dir(export_dir, staged_id) / "upload.enc"


def extracted_dir(export_dir: Path, staged_id: str) -> Path:
    """Where the archive is safely extracted to (apply reads db.dump + files/ from here)."""
    return staging_dir(export_dir, staged_id) / "extracted"


# --------------------------------------------------------------------------------------------------
# Decrypt
# --------------------------------------------------------------------------------------------------


def decrypt_argv(in_path: Path, out_path: Path) -> list[str]:
    """The openssl argv that DECRYPTS ``in_path`` (the uploaded ciphertext) to ``out_path`` (the
    plaintext .tgz). Mirrors export.encrypt_argv with ``-d``; passphrase on stdin only (-pass stdin)
    so it is never on the command line, written to disk, or logged."""
    return [
        "openssl",
        "enc",
        "-d",
        "-aes-256-cbc",
        "-pbkdf2",
        "-salt",
        "-pass",
        "stdin",
        "-in",
        str(in_path),
        "-out",
        str(out_path),
    ]


def decrypt_archive(enc_path: Path, plaintext_path: Path, passphrase: str) -> None:
    """Decrypt ``enc_path`` to ``plaintext_path`` (0600) with ``passphrase`` (piped on stdin only).

    Raises ValueError on the WRONG passphrase / corrupt ciphertext (a clear, non-secret error). The
    passphrase is never logged, written to disk, or placed on the command line."""
    fd = os.open(plaintext_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        decrypt_argv(enc_path, plaintext_path),
        input=(passphrase + "\n").encode("utf-8"),
        capture_output=True,
        timeout=1800,
        check=False,
    )
    if proc.returncode != 0:
        plaintext_path.unlink(missing_ok=True)
        # openssl reports "bad decrypt" on a wrong passphrase; never surface the passphrase or argv.
        raise ValueError("could not decrypt the archive - wrong passphrase or corrupt file")


# --------------------------------------------------------------------------------------------------
# Safe extraction (the real gate)
# --------------------------------------------------------------------------------------------------


def _is_within(root: Path, target: Path) -> bool:
    """True iff ``target`` (resolved, not following into the fs) is at or under ``root``. Pure path
    arithmetic so it does not require the target to exist."""
    try:
        root_r = root.resolve()
        target_r = (root / target).resolve() if not target.is_absolute() else target.resolve()
    except (OSError, RuntimeError):
        return False
    return root_r == target_r or root_r in target_r.parents


def assert_member_safe(member: tarfile.TarInfo, dest_root: Path) -> None:
    """Reject a single hostile tar member. Raises UnsafeArchiveError on any violation:
    absolute path, ``..`` traversal, symlink, hardlink, device/fifo/special, or a name resolving
    outside ``dest_root``. The independent authority - we never trust the archive or the stdlib."""
    name = member.name
    # Absolute paths (POSIX or Windows-style) are always rejected.
    if name.startswith("/") or name.startswith("\\") or (len(name) > 1 and name[1] == ":"):
        raise UnsafeArchiveError(f"absolute path in archive: {name!r}")
    # Any traversal component.
    parts = Path(name).parts
    if ".." in parts:
        raise UnsafeArchiveError(f"path traversal in archive: {name!r}")
    # Only regular files and directories are allowed - no links or special files.
    if member.issym() or member.islnk():
        raise UnsafeArchiveError(f"link member in archive (not allowed): {name!r}")
    if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
        raise UnsafeArchiveError(f"device/special member in archive (not allowed): {name!r}")
    if not (member.isfile() or member.isdir()):
        raise UnsafeArchiveError(f"unsupported member type in archive: {name!r}")
    # Final belt: the resolved destination must stay within dest_root.
    if not _is_within(dest_root, Path(name)):
        raise UnsafeArchiveError(f"member escapes the staging root: {name!r}")


def safe_extract(plaintext_tgz: Path, dest_root: Path) -> tuple[int, int]:
    """Safely extract ``plaintext_tgz`` into ``dest_root``. Returns (member_count, total_bytes).

    Two-pass + streamed guards. The manual validator (assert_member_safe + the entry/size/ratio
    caps) is the gate; ``tarfile.extractall(filter="data")`` adds defense in depth. Raises
    UnsafeArchiveError on any violation (the caller cleans up the staging dir)."""
    dest_root.mkdir(parents=True, exist_ok=True)
    compressed_size = plaintext_tgz.stat().st_size
    member_count = 0
    total_bytes = 0
    with tarfile.open(plaintext_tgz, mode="r:gz") as tar:
        members = tar.getmembers()
        if len(members) > MAX_ENTRIES:
            raise UnsafeArchiveError(f"too many entries ({len(members)} > {MAX_ENTRIES})")
        for member in members:
            assert_member_safe(member, dest_root)
            member_count += 1
            if member.isfile():
                total_bytes += member.size
                if total_bytes > MAX_TOTAL_UNCOMPRESSED:
                    raise UnsafeArchiveError(
                        f"archive exceeds the max uncompressed size ({MAX_TOTAL_UNCOMPRESSED} B)"
                    )
        # Decompression-bomb guard: expanded:compressed ratio. Skip for tiny archives where gzip
        # framing dominates (a few-byte payload can legitimately have a high ratio).
        if compressed_size > 4096 and total_bytes > MAX_COMPRESSION_RATIO * compressed_size:
            raise UnsafeArchiveError(
                f"suspicious compression ratio ({total_bytes}/{compressed_size})"
            )
        # Re-open and extract with the stdlib data filter as a second, independent layer.
        tar.extractall(path=dest_root, filter="data")  # noqa: S202 - members pre-validated above
    return member_count, total_bytes


# --------------------------------------------------------------------------------------------------
# Manifest + version validation
# --------------------------------------------------------------------------------------------------


def _read_manifest(extracted: Path) -> BackupManifest:
    """Load + parse extracted/manifest.json. Raises ValueError when missing/unparseable."""
    path = extracted / "manifest.json"
    if not path.is_file():
        raise ValueError("archive is missing manifest.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return BackupManifest.model_validate(raw)
    except (OSError, ValueError) as exc:
        raise ValueError("manifest.json is unreadable or malformed") from exc


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def verify_members(extracted: Path, members: list[BackupManifestMember]) -> list[str]:
    """Recompute each member's sha256 and size against the manifest. Returns a list of human error
    strings (empty == all members intact). Bounded memory (streamed). Missing/extra files are also
    reported. No member name is interpreted as a path outside ``extracted`` (already gated)."""
    errors: list[str] = []
    for member in members:
        target = extracted / member.name
        if not target.is_file():
            errors.append(f"missing member: {member.name}")
            continue
        sha, size = _sha256_file(target)
        if sha != member.sha256 or size != member.size:
            errors.append(f"checksum/size mismatch: {member.name}")
    # An extra file under files/ that the manifest does not list is a tamper signal too.
    expected = {m.name for m in members} | {"manifest.json"}
    for path in extracted.rglob("*"):
        if path.is_file():
            rel = path.relative_to(extracted).as_posix()
            if rel not in expected:
                errors.append(f"unexpected member not in manifest: {rel}")
    return errors


def validate_manifest_integrity(manifest: BackupManifest, secrets_key: str) -> list[str]:
    """Recompute the manifest HMAC over the member checksums and compare it to the stored tag.
    Returns errors (empty == intact). Refusing on mismatch catches both corruption AND tampering
    that swapped a member + its manifest entry consistently (the HMAC is keyed by the secret)."""
    errors: list[str] = []
    expected = _manifest_hmac(secrets_key, manifest.members)
    if not manifest.manifest_hmac:
        errors.append("manifest is missing its integrity tag")
    elif expected != manifest.manifest_hmac:
        # When this box's secrets key differs from the one that produced the archive, a keyed HMAC
        # can't be reproduced here. That is a SEPARATE, surfaced condition (secrets_key_match);
        # but a hard integrity check still must fail closed, so report it as an error.
        errors.append("manifest integrity check failed (corruption, tampering, or key mismatch)")
    return errors


def check_compatibility(
    manifest: BackupManifest, *, running_schema_version: int
) -> tuple[bool, list[str], list[str]]:
    """Version-compatibility gate. Returns (compatible, errors, warnings).

    Hard errors (incompatible): the archive's Postgres major != 17, or its app schema generation
    is NEWER than the running code (restoring a newer dump into older code is unsafe).
    Older-or-equal is compatible (the apply step migrates forward). An UNKNOWN archive schema (0,
    a Phase-1 archive) is treated as compatible. ``running_schema_version`` of 0 disables the
    schema gate (best-effort)."""
    errors: list[str] = []
    warnings: list[str] = []
    pg_major = _pg_major(manifest.pg_version)
    if pg_major is None:
        warnings.append("archive does not record a Postgres version; cannot verify pg major")
    elif pg_major != REQUIRED_PG_MAJOR:
        errors.append(
            f"Postgres major mismatch: archive is pg{pg_major}, this box requires "
            f"pg{REQUIRED_PG_MAJOR}"
        )
    archive_schema = manifest.app_schema_version
    if archive_schema and running_schema_version and archive_schema > running_schema_version:
        errors.append(
            f"archive schema generation ({archive_schema}) is NEWER than this code "
            f"({running_schema_version}); upgrade DokTok before restoring"
        )
    compatible = not errors
    return compatible, errors, warnings


def _pg_major(pg_version: str) -> int | None:
    """The integer major from a pg version string ('17.2' -> 17), or None when unparseable."""
    head = (pg_version or "").strip().split(".")[0]
    try:
        return int(head)
    except ValueError:
        return None


# --------------------------------------------------------------------------------------------------
# Orchestration: decrypt -> extract -> validate (NON-destructive, runs in the backend)
# --------------------------------------------------------------------------------------------------


def validate_staged_upload(
    export_dir: Path,
    staged_id: str,
    passphrase: str,
    *,
    secrets_key: str,
    running_schema_version: int,
) -> StagedValidation:
    """Decrypt + safely extract + validate the upload already streamed to upload_path(). Returns a
    StagedValidation; NEVER raises (failures land in ``errors`` and ``ok`` is False). On a HARD
    failure the extracted tree is removed but the staging dir is retained briefly (TTL) so the
    caller can still report status; on success the extracted tree is kept for the apply step."""
    sdir = staging_dir(export_dir, staged_id)
    enc = upload_path(export_dir, staged_id)
    extracted = extracted_dir(export_dir, staged_id)
    result = StagedValidation(staged_id=staged_id, staging_dir=sdir)
    plaintext = sdir / "archive.tgz"
    try:
        decrypt_archive(enc, plaintext, passphrase)
    except ValueError as exc:
        result.errors.append(str(exc))
        _cleanup_extracted(sdir, plaintext)
        _write_validation(export_dir, staged_id, result)
        return result
    finally:
        # The encrypted upload is no longer needed once decryption is attempted; drop it either way.
        enc.unlink(missing_ok=True)

    try:
        member_count, total_bytes = safe_extract(plaintext, extracted)
        result.member_count = member_count
        result.total_bytes = total_bytes
    except (UnsafeArchiveError, tarfile.TarError, OSError) as exc:
        detail = str(exc) if isinstance(exc, UnsafeArchiveError) else "archive is not a valid .tgz"
        result.errors.append(detail)
        _cleanup_extracted(sdir, plaintext)
        _write_validation(export_dir, staged_id, result)
        return result
    finally:
        plaintext.unlink(missing_ok=True)  # the plaintext .tgz is not needed once extracted

    try:
        manifest = _read_manifest(extracted)
    except ValueError as exc:
        result.errors.append(str(exc))
        _cleanup_extracted(sdir, plaintext)
        _write_validation(export_dir, staged_id, result)
        return result

    result.app_version = manifest.app_version
    result.pg_version = manifest.pg_version
    result.created_at = manifest.created_at

    # Secrets-key match is a WARNING, never a hard error (the OpenAI key won't decrypt on mismatch).
    this_fp = secrets_key_fingerprint(secrets_key)
    result.secrets_key_match = bool(this_fp) and this_fp == manifest.secrets_key_fingerprint
    if not result.secrets_key_match:
        result.warnings.append(
            "secrets key differs from the one that produced this archive; the stored OpenAI key "
            "will not be decryptable after restore (re-enter it in Settings)"
        )

    result.errors.extend(validate_manifest_integrity(manifest, secrets_key))
    result.errors.extend(verify_members(extracted, manifest.members))

    compatible, ver_errors, ver_warnings = check_compatibility(
        manifest, running_schema_version=running_schema_version
    )
    result.compatible = compatible
    result.errors.extend(ver_errors)
    result.warnings.extend(ver_warnings)

    result.ok = not result.errors and result.compatible
    if not result.ok:
        # A failed validation must not leave a restorable tree around.
        _cleanup_extracted(sdir, plaintext)
    else:
        _mark_validated(sdir)
    _write_validation(export_dir, staged_id, result)
    return result


def _cleanup_extracted(sdir: Path, plaintext: Path) -> None:
    """Remove the extracted tree + plaintext archive after a failed validation (best-effort)."""
    plaintext.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        shutil.rmtree(sdir / "extracted", ignore_errors=True)


_VALIDATED_MARKER = ".validated"
_VALIDATION_SUFFIX = ".validation.json"


def _mark_validated(sdir: Path) -> None:
    """Drop a 0600 marker so the apply step can cheaply confirm this staged_id passed preview."""
    marker = sdir / _VALIDATED_MARKER
    marker.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    os.chmod(marker, 0o600)


def is_validated(export_dir: Path, staged_id: str) -> bool:
    """True iff ``staged_id`` exists and passed preview (the .validated marker is present)."""
    return (staging_dir(export_dir, staged_id) / _VALIDATED_MARKER).is_file()


def _write_validation(export_dir: Path, staged_id: str, result: StagedValidation) -> None:
    """Persist a small validation summary (0600) for observability/debugging. No secret/passphrase;
    only the same fields the wire RestorePreview carries."""
    sdir = staging_dir(export_dir, staged_id)
    if not sdir.exists():
        return
    payload = {
        "staged_id": result.staged_id,
        "ok": result.ok,
        "compatible": result.compatible,
        "app_version": result.app_version,
        "pg_version": result.pg_version,
        "created_at": result.created_at.isoformat() if result.created_at else None,
        "member_count": result.member_count,
        "total_bytes": result.total_bytes,
        "secrets_key_match": result.secrets_key_match,
        "warnings": result.warnings,
        "errors": result.errors,
    }
    target = sdir / f"{staged_id}{_VALIDATION_SUFFIX}"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(target)


def discard_staged(export_dir: Path, staged_id: str) -> None:
    """Remove a whole staging dir (after apply consumes it, or on cleanup). Best-effort."""
    shutil.rmtree(staging_dir(export_dir, staged_id), ignore_errors=True)


_STATE_FIELDS = ("state", "step", "started_at", "finished_at", "detail", "restore_id")
_VALID_STATES = frozenset({"idle", "validating", "applying", "done", "failed"})


def read_restore_status(status_dir: Path) -> dict[str, str]:
    """Read the host-written ``restore.json`` status sentinel (OUTSIDE Postgres, same dir as the DRP
    sentinels) and project it onto the wire RestoreStatus fields. Returns the idle default when the
    file is absent/unreadable/malformed; never raises and never surfaces a path/secret.

    The DB is rewritten mid-restore, so the restore status CANNOT live in Postgres - the host script
    writes this file as it progresses and the backend only reads it (like the drill/files/pg legs).
    """
    default = {"state": "idle", "step": "", "detail": "", "restore_id": ""}
    path = status_dir / "restore.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    if not isinstance(raw, dict):
        return default
    out: dict[str, str] = dict(default)
    for key in _STATE_FIELDS:
        value = raw.get(key)
        if isinstance(value, str):
            out[key] = value
    if out.get("state") not in _VALID_STATES:
        out["state"] = "idle"
    return out


def write_restore_status(
    status_dir: Path,
    *,
    state: str,
    step: str = "",
    detail: str = "",
    restore_id: str = "",
    started_at: str = "",
    finished_at: str = "",
) -> None:
    """Atomically write the restore status sentinel (used by the backend to flip to 'validating' on
    preview; the host script overwrites it as the destructive apply progresses). 0644 so the backend
    (a different uid in compose) can read what the root helper writes. Best-effort; never raises."""
    status_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": state,
        "step": step,
        "detail": detail[:200],
        "restore_id": restore_id,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    target = status_dir / "restore.json"
    tmp = target.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(tmp, 0o644)
        tmp.replace(target)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def sweep_stale_restores(export_dir: Path, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> int:
    """Delete staging dirs older than ``ttl_seconds`` (decrypted/extracted data must not linger).
    Returns how many were removed. Best-effort; never raises."""
    root = restores_root(export_dir)
    if not root.exists():
        return 0
    cutoff = time.time() - ttl_seconds
    removed = 0
    for child in root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed
