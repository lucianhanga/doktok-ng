"""Host-script plumbing tests for deploy/restore-import.sh (M12 portable restore, Phase 2).

A full DB restore is CI-gated / manual (it needs real Postgres + the destructive importer), so these
tests assert the SAFETY PLUMBING that must hold regardless: the argument guard, the mandatory order
of the dangerous steps, and the fail-safe rollback contract. They run wherever bash exists and
self-skip otherwise. The script is invoked / inspected as a subprocess; nothing destructive runs."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "deploy" / "restore-import.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_script_exists() -> None:
    assert SCRIPT.is_file()


def test_requires_a_staged_id_argument() -> None:
    # No staged_id -> the `${1:?...}` guard makes bash exit non-zero before any work.
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={"PATH": "/usr/bin:/bin", "DOKTOK_DEPLOY_MODE": "host"},
        check=False,
    )
    assert proc.returncode != 0
    assert "usage" in (proc.stderr + proc.stdout).lower()


def test_safety_snapshot_precedes_destruction() -> None:
    """The pre-restore safety snapshot MUST come before the DB import + files swap (so a bad
    restore is always recoverable)."""
    text = _text()
    # Anchor on the step BANNERS (echo "=== restore N/5 ...") so comment mentions don't confuse the
    # ordering; the banners appear once each, in body order.
    i_snapshot = text.index('echo "=== restore 1/5')
    i_quiesce = text.index('echo "=== restore 2/5')
    i_db = text.index('echo "=== restore 3/5')
    i_files = text.index('echo "=== restore 4/5')
    assert i_snapshot < i_quiesce < i_db < i_files, (
        "steps must run snapshot -> quiesce -> db -> files"
    )
    # The pg_restore command line lives inside the DB-import step (after the snapshot banner). Slice
    # from the db-import banner so the header-comment mention of pg_restore is excluded.
    assert "pg_restore --clean" in text[i_db:], "pg_restore must run in the db-import step"


def test_aborts_when_safety_snapshot_fails() -> None:
    assert "pre-restore safety snapshot failed" in _text()
    # The abort is wired to fail_restore (the rollback path), not a bare exit.
    assert 'fail_restore "pre-restore safety snapshot failed' in _text()


def test_uses_clean_if_exists_restore() -> None:
    assert "pg_restore --clean --if-exists" in _text()
    # pgvector must be ensured before the restore.
    assert "CREATE EXTENSION IF NOT EXISTS vector" in _text()
    # Migrate forward after the dump is loaded.
    assert "python -m doktok_api migrate" in _text()


def test_quiesce_sets_maintenance_flag_and_terminates_sessions() -> None:
    text = _text()
    assert "maintenance.flag" in text
    assert "pg_terminate_backend" in text
    assert "doktok-worker quiesce" in text


def test_files_swap_is_rename_based_and_keeps_old() -> None:
    text = _text()
    assert ".old." in text  # the previous files root is kept for rollback
    assert "mv -f" in text


def test_rollback_leaves_maintenance_on_for_a_human() -> None:
    text = _text()
    # The ERR trap routes any failure through fail_restore.
    assert "trap 'fail_restore" in text
    # fail_restore must NOT clear the maintenance flag (a failed restore stays parked).
    fail_block = text[text.index("fail_restore() {") : text.index("trap 'fail_restore")]
    assert "rm -f" not in fail_block.replace("maintenance mode left ON", "")
    assert "maintenance mode left ON" in text


def test_success_path_lifts_maintenance_and_cleans_staging() -> None:
    text = _text()
    assert 'rm -f "$MAINT_FLAG"' in text  # maintenance lifted only on the success path
    assert 'rm -rf "$STAGING"' in text  # decrypted staging cleaned on success
    assert "restore success true" in text  # the success history event
    assert "restore failure false" in text  # the failure history event


def test_request_file_carries_no_passphrase() -> None:
    # The script reads only the staged_id/restore_id from the request file; it never expects a
    # passphrase there (the archive was already decrypted into staging at preview time).
    text = _text()
    assert "passphrase" not in text
