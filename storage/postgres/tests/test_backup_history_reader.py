"""Unit tests for the file-backed append-only backup history reader (M12 DRP hardening).

These do NOT need a database: ``get_backup_history`` only reads ``<status_dir>/history.jsonl`` off
the filesystem. They exercise the same parse/chain/limit/leg/truncation logic the DRP API surfaces.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

from doktok_storage_postgres.db import Database
from doktok_storage_postgres.repositories import PostgresAppSettingsRepository


def _line(seq: int, prev: str, leg: str, event: str, ok: bool = True) -> str:
    return json.dumps(
        {
            "schema": 1,
            "seq": seq,
            "prev_sha256": prev,
            "ts": f"2026-06-26T03:00:{seq:02d}Z",
            "leg": leg,
            "event": event,
            "ok": ok,
            "detail": "x",
        }
    )


def _chained(records: list[tuple[str, str]]) -> list[str]:
    """Build a valid prev_sha256-chained set of history lines from (leg, event) pairs."""
    lines: list[str] = []
    prev = ""
    for i, (leg, event) in enumerate(records, start=1):
        line = _line(i, prev, leg, event)
        lines.append(line)
        prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
    return lines


def _repo(status_dir: Path) -> PostgresAppSettingsRepository:
    # db is never touched by get_backup_history; cast to satisfy the typed constructor.
    return PostgresAppSettingsRepository(cast(Database, None), backup_status_dir=str(status_dir))


def test_missing_file_is_empty_not_available(tmp_path: Path) -> None:
    events, available, truncated, integrity = _repo(tmp_path).get_backup_history()
    assert events == [] and available is False and truncated is False and integrity is True


def test_no_status_dir_configured(tmp_path: Path) -> None:
    events, available, _trunc, integrity = PostgresAppSettingsRepository(
        cast(Database, None)
    ).get_backup_history()
    assert events == [] and available is False and integrity is True


def test_newest_first_and_malformed_line_skipped(tmp_path: Path) -> None:
    lines = _chained([("files", "success"), ("pg", "success")])
    # Inject a malformed line in the middle - it must be skipped, not crash, and not appear.
    content = "\n".join([lines[0], "{not json", lines[1]]) + "\n"
    (tmp_path / "history.jsonl").write_text(content, encoding="utf-8")
    events, available, _trunc, _integ = _repo(tmp_path).get_backup_history()
    assert available is True
    assert [e["leg"] for e in events] == ["pg", "files"]  # newest-first


def test_limit_clamp(tmp_path: Path) -> None:
    lines = _chained([("files", "success") for _ in range(10)])
    (tmp_path / "history.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    events, _avail, _trunc, _integ = _repo(tmp_path).get_backup_history(limit=3)
    assert len(events) == 3


def test_leg_filter(tmp_path: Path) -> None:
    lines = _chained([("files", "success"), ("pg", "success"), ("files", "failure")])
    (tmp_path / "history.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    events, _avail, _trunc, _integ = _repo(tmp_path).get_backup_history(leg="files")
    assert {e["leg"] for e in events} == {"files"}
    assert len(events) == 2


def test_truncated_on_large_file(tmp_path: Path) -> None:
    # Build a file well over the 256 KiB read cap so the reader reports truncation.
    lines = _chained([("files", "success") for _ in range(4000)])
    blob = "\n".join(lines) + "\n"
    assert len(blob.encode("utf-8")) > 256 * 1024
    (tmp_path / "history.jsonl").write_text(blob, encoding="utf-8")
    events, available, truncated, _integ = _repo(tmp_path).get_backup_history(limit=500)
    assert available is True and truncated is True and len(events) <= 500


def test_integrity_false_on_broken_chain(tmp_path: Path) -> None:
    lines = _chained([("files", "success"), ("pg", "success"), ("files", "success")])
    # Corrupt the middle line's prev_sha256 so the chain no longer verifies.
    bad = json.loads(lines[1])
    bad["prev_sha256"] = "deadbeef"
    lines[1] = json.dumps(bad)
    (tmp_path / "history.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _events, available, _trunc, integrity = _repo(tmp_path).get_backup_history()
    assert available is True and integrity is False


def test_integrity_true_on_valid_chain(tmp_path: Path) -> None:
    lines = _chained([("files", "success"), ("pg", "success")])
    (tmp_path / "history.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _events, _avail, _trunc, integrity = _repo(tmp_path).get_backup_history()
    assert integrity is True
