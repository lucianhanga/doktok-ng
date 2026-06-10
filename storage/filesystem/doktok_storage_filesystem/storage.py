"""Local filesystem adapters: storage, hashing, and quarantine."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from doktok_core.ingestion.layout import FilesystemLayout

_CHUNK = 1024 * 1024


class LocalFileStorage:
    """``FileStorage`` over the local filesystem. Moves are atomic on the same filesystem."""

    def move(self, source: str, destination: str) -> None:
        dst = Path(destination)
        dst.parent.mkdir(parents=True, exist_ok=True)
        # os.replace is atomic within a filesystem and works for files and directories.
        os.replace(source, destination)

    def read_bytes(self, path: str) -> bytes:
        return Path(path).read_bytes()


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
