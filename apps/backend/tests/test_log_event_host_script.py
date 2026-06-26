"""Host-script tests for deploy/lib.sh ``log_event`` (M12 DRP hardening).

These exercise the bash append-only-history helper directly via a subprocess, so the JSON shape, the
escaping of free-text detail, the monotonic ``seq``, the ``prev_sha256`` chain, and the rotation to
``.1`` are all proven end-to-end. They run wherever bash + sha256sum/shasum exist (CI ubuntu,
macOS); they self-skip otherwise.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_SH = REPO_ROOT / "deploy" / "lib.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None
    or (shutil.which("sha256sum") is None and shutil.which("shasum") is None),
    reason="needs bash + sha256sum/shasum",
)


def _run(status_dir: Path, script: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
    env = {
        "DOKTOK_BACKUP_DIR": str(status_dir.parent),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        **extra_env,
    }
    body = f"set -euo pipefail\nsource '{LIB_SH}'\nSTATUS_DIR='{status_dir}'\n{script}\n"
    return subprocess.run(["bash", "-c", body], capture_output=True, text=True, env=env, check=True)


def _lines(status_dir: Path) -> list[str]:
    return [ln for ln in (status_dir / "history.jsonl").read_text().splitlines() if ln.strip()]


def test_emits_valid_json_with_whitelisted_fields(tmp_path: Path) -> None:
    status = tmp_path / "status"
    _run(
        status,
        'log_event files success true "restic snapshot" \'"size":"662 MiB","item_count":287\'',
    )
    lines = _lines(status)
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["leg"] == "files" and rec["event"] == "success" and rec["ok"] is True
    assert rec["seq"] == 1 and rec["prev_sha256"] == "" and rec["schema"] == 1
    assert rec["size"] == "662 MiB" and rec["item_count"] == 287
    assert rec["detail"] == "restic snapshot"


def test_detail_with_quote_and_newline_is_escaped(tmp_path: Path) -> None:
    status = tmp_path / "status"
    # A detail containing a double-quote and a newline must stay one valid JSON line.
    _run(status, 'log_event pg failure false "oops \\"quoted\\" $(printf \'a\\nb\')"')
    lines = _lines(status)
    assert len(lines) == 1  # the embedded newline did NOT split the record into two lines
    rec = json.loads(lines[0])  # parses cleanly -> escaping is correct
    assert '"quoted"' in rec["detail"]
    assert "\n" in rec["detail"]  # the literal newline survived as an escaped \n then decoded


def test_seq_increments_and_chain_links(tmp_path: Path) -> None:
    status = tmp_path / "status"
    _run(
        status,
        'log_event files start true "a"\n'
        'log_event files success true "b"\n'
        'log_event pg success true "c"',
    )
    lines = _lines(status)
    assert [json.loads(x)["seq"] for x in lines] == [1, 2, 3]
    # Each line's prev_sha256 == sha256 of the previous raw line.
    assert json.loads(lines[0])["prev_sha256"] == ""
    for i in range(1, len(lines)):
        expected = hashlib.sha256(lines[i - 1].encode("utf-8")).hexdigest()
        assert json.loads(lines[i])["prev_sha256"] == expected


def test_rotation_rolls_to_dot_one_and_continues_chain(tmp_path: Path) -> None:
    status = tmp_path / "status"
    # Force rotation at a tiny threshold: write 3 lines with max=2 -> the 3rd rotates.
    _run(
        status,
        "export DOKTOK_HISTORY_MAX_LINES=2\n"
        "HISTORY_MAX_LINES=2\n"
        'log_event files success true "a"\n'
        'log_event files success true "b"\n'
        'log_event files success true "c"',
        DOKTOK_HISTORY_MAX_LINES="2",
    )
    archived = status / "history.jsonl.1"
    current = status / "history.jsonl"
    assert archived.is_file() and current.is_file()
    arch_lines = [ln for ln in archived.read_text().splitlines() if ln.strip()]
    cur_lines = _lines(status)
    assert len(arch_lines) == 2  # the first two rolled over
    assert len(cur_lines) == 1  # the third started the fresh file
    # The fresh file's first line chains off the last archived line, and seq keeps climbing.
    first_new = json.loads(cur_lines[0])
    assert first_new["seq"] == 3
    assert first_new["prev_sha256"] == hashlib.sha256(arch_lines[-1].encode("utf-8")).hexdigest()
