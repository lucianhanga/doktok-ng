"""Build a portable, encrypted one-file backup archive (M12 portable backup, Phase 1: export only).

Topology: the BACKEND builds the export itself because export touches nothing destructive (it is a
read-only pg_dump + a read of the mounted files_root). This works uniformly in both compose mode
(the files volume + db are reachable from the backend) and host mode - no root, no docker socket.

Archive layout (a gzipped tar):
  db.dump        custom-format ``pg_dump --format=custom --clean --if-exists`` of the app DB;
                 portable into a fresh pg17+pgvector DB (HNSW rebuilds on restore).
  files/...      the files_root document tree (DOKTOK_FILES_ROOT) under a ``files/`` prefix.
  manifest.json  schema_version, created_at, app/pg versions, per-member name+size+sha256, a
                 manifest-level HMAC over the member checksums, and a non-reversible
                 secrets_key_fingerprint (so a later restore can warn on a key mismatch).

Data-only: the archive NEVER contains DOKTOK_SECRETS_KEY, tenant tokens, DATABASE_URL, or .env. The
OpenAI key inside app_settings stays as its existing Fernet ciphertext.

The build is bounded-memory: pg_dump streams to a file, the files tree is added entry-by-entry, and
checksums are computed by streaming each member. The staged PLAINTEXT archive is written 0600 and is
encrypted only at the download boundary (see :func:`encrypt_argv`). A TTL sweep removes stale staged
archives. Status is tracked in ``<export_id>.status.json`` so the async build is pollable.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os

# subprocess: pg_dump/psql/openssl are run with a fixed argv (no shell, no user input). Re-exported
# (redundant alias) so tests can monkeypatch ``export.subprocess.run`` under strict mypy.
import subprocess as subprocess
import tarfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# BackupExportInfo is re-exported (used by tests + the router as ``export.BackupExportInfo``).
from doktok_contracts.schemas import BackupExportInfo as BackupExportInfo
from doktok_contracts.schemas import BackupManifest, BackupManifestMember

from doktok_core.backup.fingerprint import secrets_key_fingerprint
from doktok_core.security.keys import derive_key

logger = logging.getLogger("doktok.backup.export")

_CHUNK = 1024 * 1024  # 1 MiB streaming chunk for checksums
_STATUS_SUFFIX = ".status.json"
_ARCHIVE_SUFFIX = ".tgz"
DEFAULT_TTL_SECONDS = 24 * 3600  # staged plaintext archives older than this are swept


@dataclass(frozen=True)
class ExportPaths:
    """Inputs the builder needs. ``export_dir`` MUST be on a writable volume (the backend mounts the
    backup repo read-only; the staged plaintext export goes to a separate writable dir)."""

    export_dir: Path
    files_root: Path
    database_url: str
    secrets_key: str
    app_version: str
    # The DB schema/migration generation this deployment ships (latest migration number). Stamped
    # into the manifest so a later restore can refuse a NEWER archive. 0 = unknown (omit the gate).
    app_schema_version: int = 0


def _read_status(path: Path) -> BackupExportInfo | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return BackupExportInfo.model_validate(raw)
    except ValueError:
        return None


def _write_status(export_dir: Path, info: BackupExportInfo) -> None:
    """Persist the export status atomically (write-temp-then-rename) at 0600."""
    export_dir.mkdir(parents=True, exist_ok=True)
    target = export_dir / f"{info.export_id}{_STATUS_SUFFIX}"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(info.model_dump_json(), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(target)


def read_export_status(export_dir: Path, export_id: str) -> BackupExportInfo | None:
    """The current status of an export build, or None if unknown."""
    return _read_status(export_dir / f"{export_id}{_STATUS_SUFFIX}")


def latest_export_status(export_dir: Path) -> BackupExportInfo | None:
    """The most recently created export status (by created_at, then file mtime), or None."""
    if not export_dir.exists():
        return None
    candidates: list[tuple[float, BackupExportInfo]] = []
    for status_file in export_dir.glob(f"*{_STATUS_SUFFIX}"):
        info = _read_status(status_file)
        if info is None:
            continue
        order = info.created_at.timestamp() if info.created_at else status_file.stat().st_mtime
        candidates.append((order, info))
    if not candidates:
        return None
    return max(candidates, key=lambda c: c[0])[1]


_BUILDING_LOCK = "_building.lock"  # the single global slot for an in-flight build (#629)


def is_build_in_progress(export_dir: Path) -> bool:
    """True if any export is currently in the 'building' state (single-flight guard). A
    'building' status or claim lock older than the TTL is STALE (the process died mid-build,
    F-24 #629): it is swept and treated as not-in-progress, so a crashed build can never wedge
    the feature at 429 forever."""
    if not export_dir.exists():
        return False
    cutoff = time.time() - DEFAULT_TTL_SECONDS
    lock = export_dir / _BUILDING_LOCK
    if lock.exists():
        if lock.stat().st_mtime < cutoff:
            lock.unlink(missing_ok=True)
        else:
            return True
    for status_file in export_dir.glob(f"*{_STATUS_SUFFIX}"):
        info = _read_status(status_file)
        if info is None or info.status != "building":
            continue
        created = info.created_at.timestamp() if info.created_at else status_file.stat().st_mtime
        if created < cutoff:
            status_file.unlink(missing_ok=True)
            continue
        return True
    return False


def claim_build(export_dir: Path, export_id: str, app_version: str) -> BackupExportInfo | None:
    """Atomically claim the single export build slot (F-24 #629): the claim lock is created with
    O_CREAT|O_EXCL in the REQUEST handler, so a concurrent starter loses instantly (429) -
    closing the check-then-act race where the status was written only inside the background
    task. Returns the info for the response on success, None when a build is already claimed.
    The background task MUST call :func:`release_build` when it finishes (success or failure)."""
    export_dir.mkdir(parents=True, exist_ok=True)
    if is_build_in_progress(export_dir):  # fast path; the O_EXCL below is the atomic one
        return None
    lock = export_dir / _BUILDING_LOCK
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(export_id)
    return BackupExportInfo(
        export_id=export_id,
        status="building",
        created_at=datetime.now(UTC),
        app_version=app_version,
    )


def release_build(export_dir: Path) -> None:
    """Free the build slot claimed by :func:`claim_build` (best-effort, idempotent)."""
    (export_dir / _BUILDING_LOCK).unlink(missing_ok=True)


def staged_archive_path(export_dir: Path, export_id: str) -> Path:
    """Path to the staged PLAINTEXT archive for ``export_id`` (may not exist yet)."""
    return export_dir / f"{export_id}{_ARCHIVE_SUFFIX}"


def _pg_version(database_url: str) -> str:
    """Best-effort server version string (e.g. '17.2') for the manifest; never raises."""
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["psql", database_url, "-tAc", "SHOW server_version"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip().split()[0] if out.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _sha256_file(path: Path) -> tuple[str, int]:
    """Streaming (sha256-hex, size-bytes) of a file without loading it whole."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _manifest_hmac(secrets_key: str, members: list[BackupManifestMember]) -> str:
    """HMAC-SHA256 (hex) over the sorted ``name:sha256`` member lines. Keyed by the secrets key when
    present (tamper-evident + bound to the deployment); falls back to a plain digest when no key is
    configured so the field is still a stable integrity check in dev."""
    import hmac

    lines = "\n".join(sorted(f"{m.name}:{m.sha256}" for m in members)).encode("utf-8")
    if secrets_key:
        # Keyed by the purpose-separated manifest subkey (#631, F-16), not the raw secrets key.
        return hmac.new(derive_key(secrets_key, "manifest"), lines, hashlib.sha256).hexdigest()
    return hashlib.sha256(lines).hexdigest()


def build_export(paths: ExportPaths, export_id: str) -> BackupExportInfo:
    """Build the staged plaintext archive for ``export_id`` and return its final status.

    Writes status='building' first, then assembles db.dump + files/ + manifest.json into a single
    gzipped tar at 0600. On any failure the staged archive is removed and status='failed' (with a
    short, non-secret error) is returned - never raised - so the async runner can record it.
    """
    export_dir = paths.export_dir
    created = datetime.now(UTC)
    _write_status(
        export_dir,
        BackupExportInfo(
            export_id=export_id,
            status="building",
            created_at=created,
            app_version=paths.app_version,
        ),
    )
    archive = staged_archive_path(export_dir, export_id)
    work = export_dir / f"{export_id}.work"
    db_dump = work / "db.dump"
    try:
        work.mkdir(parents=True, exist_ok=True)
        os.chmod(work, 0o700)
        _run_pg_dump(paths.database_url, db_dump)
        members = _assemble_archive(archive, db_dump, paths, export_id, created)
        size = archive.stat().st_size
        info = BackupExportInfo(
            export_id=export_id,
            status="ready",
            created_at=created,
            size_bytes=size,
            app_version=paths.app_version,
            pg_version=_pg_version(paths.database_url),
            member_count=len(members),
        )
        _write_status(export_dir, info)
        logger.info(
            "portable export %s ready (%d bytes, %d members)", export_id, size, len(members)
        )
        return info
    except Exception as exc:  # noqa: BLE001 - report failure as status, never crash the runner
        archive.unlink(missing_ok=True)
        # Keep the message short and non-secret: never echo the DSN or the subprocess command line.
        detail = _safe_error(exc)
        logger.warning("portable export %s failed: %s", export_id, detail)
        info = BackupExportInfo(
            export_id=export_id,
            status="failed",
            created_at=created,
            app_version=paths.app_version,
            error=detail,
        )
        _write_status(export_dir, info)
        return info
    finally:
        # Remove the intermediate plaintext db.dump + work dir; the assembled archive stays.
        db_dump.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            work.rmdir()


def _run_pg_dump(database_url: str, out: Path) -> None:
    """Stream a custom-format pg_dump to ``out`` (0600). Matches deploy/backup-pg-logical.sh:
    ``--format=custom --clean --if-exists``. Raises on a non-zero exit (caught by build_export)."""
    proc = subprocess.run(  # noqa: S603 - fixed argv (no shell); database_url is config, not input
        [  # noqa: S607
            "pg_dump",
            "--format=custom",
            "--clean",
            "--if-exists",
            "-d",
            database_url,
            "-f",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    if proc.returncode != 0:
        # stderr can carry the DSN host; keep only the last short line and strip anything URL-ish.
        raise RuntimeError(f"pg_dump exited {proc.returncode}")
    os.chmod(out, 0o600)


def _assemble_archive(
    archive: Path,
    db_dump: Path,
    paths: ExportPaths,
    export_id: str,
    created: datetime,
) -> list[BackupManifestMember]:
    """Tar+gzip db.dump + the files_root tree (under ``files/``) + manifest.json into ``archive``
    (0600). Returns the member list. Per-file checksums are streamed (bounded memory)."""
    members: list[BackupManifestMember] = []

    # db.dump member.
    dump_sha, dump_size = _sha256_file(db_dump)
    members.append(BackupManifestMember(name="db.dump", size=dump_size, sha256=dump_sha))

    # files/ members: walk the tree deterministically; checksum each regular file.
    files_root = paths.files_root
    file_entries: list[tuple[Path, str]] = []  # (abs path, in-archive name)
    if files_root.exists():
        for abs_path in sorted(p for p in files_root.rglob("*") if p.is_file()):
            rel = abs_path.relative_to(files_root).as_posix()
            arcname = f"files/{rel}"
            sha, size = _sha256_file(abs_path)
            members.append(BackupManifestMember(name=arcname, size=size, sha256=sha))
            file_entries.append((abs_path, arcname))

    manifest = BackupManifest(
        schema_version=1,
        created_at=created,
        app_version=paths.app_version,
        pg_version=_pg_version(paths.database_url),
        app_schema_version=paths.app_schema_version,
        members=members,
        manifest_hmac=_manifest_hmac(paths.secrets_key, members),
        secrets_key_fingerprint=secrets_key_fingerprint(paths.secrets_key),
    )

    tmp_archive = archive.with_suffix(archive.suffix + ".tmp")
    # 0600 from the start: create the file restricted, then open it for the tar writer.
    fd = os.open(tmp_archive, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
            tar.add(str(db_dump), arcname="db.dump")
            for abs_path, arcname in file_entries:
                tar.add(str(abs_path), arcname=arcname, recursive=False)
            manifest_bytes = manifest.model_dump_json(indent=2).encode("utf-8")
            info = tarfile.TarInfo(name="manifest.json")
            info.size = len(manifest_bytes)
            info.mtime = int(created.timestamp())
            import io

            tar.addfile(info, io.BytesIO(manifest_bytes))
    except BaseException:
        tmp_archive.unlink(missing_ok=True)
        raise
    os.chmod(tmp_archive, 0o600)
    tmp_archive.replace(archive)
    return members


def encrypt_argv(in_path: Path, out_path: Path) -> list[str]:
    """The openssl argv that encrypts ``in_path`` (the staged plaintext archive) to ``out_path`` at
    the download boundary.

    AES-256-CBC with PBKDF2-HMAC-SHA256 at 600,000 iterations (F-17 #632, the OWASP-current
    recommendation - the openssl default of 10k made a stolen archive ~60x cheaper to crack) and
    a random salt; the passphrase is supplied on stdin (``-pass stdin``) so it is NEVER on the
    command line, written to disk, or logged. The data is read from ``-in`` and written to
    ``-out`` (NOT stdin/stdout) so the passphrase line cannot be mistaken for data - a robust,
    version-portable invocation. The caller pipes ONLY the passphrase line on stdin.
    Decrypt: ``openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha256 -salt -pass stdin
    -in <file> -out <plain>`` (pre-#632 archives used the default count; restore falls back)."""
    return [
        "openssl",
        "enc",
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
        str(in_path),
        "-out",
        str(out_path),
    ]


def sweep_stale_exports(export_dir: Path, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> int:
    """Delete staged archives + their status files older than ``ttl_seconds`` (plaintext at rest
    must not linger). Returns how many archives were removed. Best-effort; never raises."""
    if not export_dir.exists():
        return 0
    cutoff = time.time() - ttl_seconds
    removed = 0
    for archive in export_dir.glob(f"*{_ARCHIVE_SUFFIX}"):
        try:
            if archive.stat().st_mtime < cutoff:
                export_id = archive.name[: -len(_ARCHIVE_SUFFIX)]
                archive.unlink(missing_ok=True)
                (export_dir / f"{export_id}{_STATUS_SUFFIX}").unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


def discard_export(export_dir: Path, export_id: str) -> None:
    """Remove a staged archive + its status (after a successful download). Best-effort."""
    staged_archive_path(export_dir, export_id).unlink(missing_ok=True)
    (export_dir / f"{export_id}{_STATUS_SUFFIX}").unlink(missing_ok=True)


def _safe_error(exc: Exception) -> str:
    """A short, non-secret one-liner for the status. Strips anything that looks like a DSN/URL."""
    text = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    if "://" in text:  # never surface a connection string fragment
        text = "build failed"
    return text[:200]
