"""Local filesystem adapters: storage, hashing, and quarantine."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from doktok_core.ingestion.layout import FilesystemLayout

_CHUNK = 1024 * 1024


def _fsync_path(target: Path) -> None:
    """Flush a just-written/moved file and its parent directory to disk (APP-C1).

    Without this the bytes/dirent can sit in the OS page cache while Postgres has already fsync'd
    the 'active' document row - so a hard crash could leave a committed row whose artifact bytes
    never reached disk. fsync'ing here keeps the "artifacts durable before the active row" ordering
    real. The directory fsync (persisting the rename/create) is best-effort: some platforms reject
    fsync on a directory fd.
    """
    fd = os.open(target, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass  # directory fsync unsupported on this platform; the file fsync above still holds


class LocalFileStorage:
    """``FileStorage`` over the local filesystem. Moves are atomic on the same filesystem; writes
    and moves fsync so artifacts are durable before the row that points at them (APP-C1)."""

    def move(self, source: str, destination: str) -> None:
        dst = Path(destination)
        dst.parent.mkdir(parents=True, exist_ok=True)
        # os.replace is atomic within a filesystem and works for files and directories.
        os.replace(source, destination)
        _fsync_path(dst)

    def read_bytes(self, path: str) -> bytes:
        return Path(path).read_bytes()

    def write_bytes(self, path: str, data: bytes) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        _fsync_path(target)

    def write_text(self, path: str, text: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        _fsync_path(target)


class Sha256HashService:
    """``HashService`` computing a streaming SHA-256 so large files are not held in memory."""

    def sha256(self, path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            while chunk := handle.read(_CHUNK):
                digest.update(chunk)
        return digest.hexdigest()


class QuarantineService:
    """``QuarantineService`` that isolates a job's working directory under quarantine/."""

    def __init__(self, layout: FilesystemLayout) -> None:
        self._layout = layout

    def quarantine(self, path: str, reason: str) -> None:
        # ``path`` is the job working directory; move it wholesale into quarantine/.
        source = Path(path)
        destination = self._layout.quarantine / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
