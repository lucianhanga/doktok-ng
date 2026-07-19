"""staged_id validation in deploy/restore-import.sh (#641, security audit F-29).

The root restore importer takes staged_id from a host-writable request file (the systemd unit
sed-extracts it; the requests/ dir is backend-writable) and interpolates it into paths - a
'../../x' id made root run pg_restore on an attacker-supplied dump and `rm -rf` the traversed
directory. The script now refuses anything but the backend's uuid4-hex format BEFORE any path
use. These tests run the real script with every directory pointed at tmp_path.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "restore-import.sh"


def _run(staged_id: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DOKTOK_BACKUP_DIR"] = str(tmp_path / "backups")
    env["DOKTOK_BACKUP_EXPORT_DIR"] = str(tmp_path / "exports")
    env["DOKTOK_FILES_ROOT"] = str(tmp_path / "files")
    return subprocess.run(
        ["bash", str(SCRIPT), staged_id],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_traversal_staged_id_is_refused_before_any_path_use(tmp_path: Path) -> None:
    proc = _run("../../evil", tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2
    assert "invalid staged_id" in out
    # Nothing was touched: no status sentinel, no history, no directories created.
    assert not (tmp_path / "backups").exists()
    assert not (tmp_path / "exports").exists()


def test_non_hex_staged_id_is_refused(tmp_path: Path) -> None:
    proc = _run("not-hex-at-all", tmp_path)
    assert proc.returncode == 2
    assert "invalid staged_id" in (proc.stdout + proc.stderr)


def test_well_formed_id_passes_the_format_gate(tmp_path: Path) -> None:
    # A uuid4-hex id gets PAST the format check and fails later on the missing staged dump -
    # proving the gate rejects bad formats, not every call.
    proc = _run("a" * 32, tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1
    assert "db.dump not found" in out
