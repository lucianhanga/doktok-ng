"""In-memory history-reader anchor tests (F-41, #653): regression + rewrite detection."""

from __future__ import annotations

import hashlib
import json

from doktok_core.settings.inmemory import InMemoryAppSettingsRepository


def _line(seq: int, prev: str, detail: str = "x") -> str:
    return json.dumps(
        {
            "schema": 1,
            "seq": seq,
            "prev_sha256": prev,
            "ts": f"2026-06-26T03:00:{seq:02d}Z",
            "leg": "files",
            "event": "success",
            "ok": True,
            "detail": detail,
        }
    )


def _chained(count: int) -> list[str]:
    lines: list[str] = []
    prev = ""
    for seq in range(1, count + 1):
        line = _line(seq, prev)
        lines.append(line)
        prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
    return lines


def test_anchor_catches_tail_deletion_and_does_not_advance() -> None:
    repo = InMemoryAppSettingsRepository()
    repo.backup_history_lines = _chained(3)
    assert repo.get_backup_history()[3] is True  # intact read anchors the head (seq 3)

    # Tail deleted: the window's own chain is still valid, but the head regressed.
    repo.backup_history_lines = repo.backup_history_lines[:2]
    assert repo.get_backup_history()[3] is False
    # The truncated state was NOT blessed: reading it again still fails.
    assert repo.get_backup_history()[3] is False


def test_anchor_catches_full_rewrite_with_recomputed_chain() -> None:
    repo = InMemoryAppSettingsRepository()
    lines = _chained(3)
    repo.backup_history_lines = lines
    assert repo.get_backup_history()[3] is True

    # The attacker rewrites line 2 AND recomputes the (unkeyed) chain consistently: the in-window
    # sha chain verifies, but the anchored head's bytes inevitably changed.
    forged = json.loads(lines[1])
    forged["detail"] = "forged"
    lines[1] = json.dumps(forged)
    head = json.loads(lines[2])
    head["prev_sha256"] = hashlib.sha256(lines[1].encode("utf-8")).hexdigest()
    lines[2] = json.dumps(head)
    repo.backup_history_lines = lines
    assert repo.get_backup_history()[3] is False
