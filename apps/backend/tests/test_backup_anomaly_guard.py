"""Host-script tests for the backup anomaly guard (#747).

The guard in deploy/lib.sh refuses to back up a database whose live document count collapsed below
half of the previous pg sentinel's ``doc_count`` - the "backing up the wreckage prunes the good
history" failure mode. Exercised directly via bash subprocess like the log_event tests, plus light
text assertions for the retention changes in backup-files.sh and pgbackrest.conf.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_SH = REPO_ROOT / "deploy" / "lib.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _run_guard(backup_dir: Path, cur: int, **extra_env: str) -> subprocess.CompletedProcess[str]:
    env = {
        "DOKTOK_BACKUP_DIR": str(backup_dir),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        **extra_env,
    }
    body = (
        f"source '{LIB_SH}'\n"
        f'backup_anomaly_guard "{cur}"\n'
    )
    return subprocess.run(["bash", "-c", body], capture_output=True, text=True, env=env)


def _write_sentinel(backup_dir: Path, doc_count: int | None) -> None:
    status = backup_dir / "status"
    status.mkdir(parents=True, exist_ok=True)
    extra = f',"doc_count":{doc_count}' if doc_count is not None else ""
    (status / "pg.json").write_text(
        '{"leg":"pg","ok":true,"last_run_at":"2026-07-24T08:50:05Z","detail":"pgbackrest full"'
        + extra
        + "}\n",
        encoding="utf-8",
    )


def test_no_sentinel_passes(tmp_path: Path) -> None:
    proc = _run_guard(tmp_path, 0)
    assert proc.returncode == 0


def test_sentinel_without_doc_count_passes(tmp_path: Path) -> None:
    _write_sentinel(tmp_path, None)
    proc = _run_guard(tmp_path, 0)
    assert proc.returncode == 0


def test_small_drop_passes(tmp_path: Path) -> None:
    _write_sentinel(tmp_path, 32)
    proc = _run_guard(tmp_path, 20)  # 20*2=40 >= 32
    assert proc.returncode == 0


def test_collapse_to_zero_aborts(tmp_path: Path) -> None:
    _write_sentinel(tmp_path, 32)
    proc = _run_guard(tmp_path, 0)
    assert proc.returncode == 1
    assert "anomaly guard" in proc.stderr
    assert "DOKTOK_BACKUP_FORCE=1" in proc.stderr


def test_space_after_colon_is_tolerated(tmp_path: Path) -> None:
    status = tmp_path / "status"
    status.mkdir(parents=True)
    (status / "pg.json").write_text(
        '{"leg": "pg", "ok": true, "doc_count": 32}\n', encoding="utf-8"
    )
    proc = _run_guard(tmp_path, 0)
    assert proc.returncode == 1


def test_big_drop_aborts(tmp_path: Path) -> None:
    _write_sentinel(tmp_path, 32)
    proc = _run_guard(tmp_path, 10)  # 10*2=20 < 32
    assert proc.returncode == 1


def test_force_override_passes(tmp_path: Path) -> None:
    _write_sentinel(tmp_path, 32)
    proc = _run_guard(tmp_path, 0, DOKTOK_BACKUP_FORCE="1")
    assert proc.returncode == 0


def test_keep_last_protects_intra_day_snapshots() -> None:
    text = (REPO_ROOT / "deploy" / "backup-files.sh").read_text(encoding="utf-8")
    assert "--keep-last 7" in text
    assert "--keep-daily 14" in text


def test_pgbackrest_retention_is_time_based() -> None:
    text = (REPO_ROOT / "deploy" / "pgbackrest" / "pgbackrest.conf").read_text(encoding="utf-8")
    assert "repo1-retention-full-type=time" in text
    assert "repo1-retention-full=30" in text


def test_backup_scripts_record_doc_count_baseline() -> None:
    backup_sh = (REPO_ROOT / "deploy" / "backup.sh").read_text(encoding="utf-8")
    backup_pg_sh = (REPO_ROOT / "deploy" / "backup-pg.sh").read_text(encoding="utf-8")
    assert "backup_anomaly_guard" in backup_sh
    assert "doc_count" in backup_sh
    assert "doc_count" in backup_pg_sh


def test_pg_wal_freshness_honours_compose_overrides() -> None:
    """The WAL-freshness stamp must run on the dev box too (#745): compose files/env overridable
    like backup.sh, never hardcoded to prod files."""
    text = (REPO_ROOT / "deploy" / "pg-wal-freshness.sh").read_text(encoding="utf-8")
    assert "DOKTOK_COMPOSE_FILES" in text
    assert "DOKTOK_COMPOSE_ENV_FILE" in text
    # and never the hardcoded prod pair again
    assert "docker-compose.prod.yml --env-file .env.production" not in text


def test_pgbackrest_log_and_lock_dirs_are_created() -> None:
    """stanza-create does NOT create the log/lock dirs; without them every pgbackrest run warns
    'unable to open log file' and logs nowhere - the scripts must create them."""
    backup_sh = (REPO_ROOT / "deploy" / "backup.sh").read_text(encoding="utf-8")
    backup_pg_sh = (REPO_ROOT / "deploy" / "backup-pg.sh").read_text(encoding="utf-8")
    restore_sh = (REPO_ROOT / "deploy" / "restore.sh").read_text(encoding="utf-8")
    assert "/var/lib/doktok/pg/log" in backup_sh
    assert "/var/lib/doktok/pg/lock" in backup_sh
    assert '"$PG_DIR/log"' in backup_pg_sh
    assert '"$PG_DIR/lock"' in backup_pg_sh
    assert "/var/lib/doktok/pg/log" in restore_sh
