"""LocalFileStorage durability: writes/moves fsync the data + parent dir (APP-C1)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from doktok_storage_filesystem.storage import LocalFileStorage

real_fsync = os.fsync


def test_write_bytes_fsyncs(tmp_path: Path) -> None:
    storage = LocalFileStorage()
    target = tmp_path / "sub" / "original.bin"
    with patch("doktok_storage_filesystem.storage.os.fsync") as fsync:
        storage.write_bytes(str(target), b"data")
    assert target.read_bytes() == b"data"
    assert fsync.call_count >= 1  # file (+ parent dir where supported)


def test_write_text_fsyncs(tmp_path: Path) -> None:
    storage = LocalFileStorage()
    target = tmp_path / "manifest.json"
    with patch("doktok_storage_filesystem.storage.os.fsync") as fsync:
        storage.write_text(str(target), "{}")
    assert target.read_text() == "{}"
    assert fsync.call_count >= 1


def test_move_fsyncs_destination(tmp_path: Path) -> None:
    storage = LocalFileStorage()
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst = tmp_path / "active" / "original.bin"
    with patch("doktok_storage_filesystem.storage.os.fsync") as fsync:
        storage.move(str(src), str(dst))
    assert dst.read_bytes() == b"x" and not src.exists()
    assert fsync.call_count >= 1


def test_directory_fsync_failure_is_tolerated(tmp_path: Path) -> None:
    # Some platforms reject fsync on a directory fd; the file fsync must still hold and not raise.
    storage = LocalFileStorage()
    target = tmp_path / "f.txt"
    calls = {"n": 0}

    def flaky(fd: int) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:  # the directory fsync (second call) fails
            raise OSError("EINVAL: fsync on directory")
        real_fsync(fd)

    with patch("doktok_storage_filesystem.storage.os.fsync", side_effect=flaky):
        storage.write_text(str(target), "ok")  # must not raise
    assert target.read_text() == "ok"
