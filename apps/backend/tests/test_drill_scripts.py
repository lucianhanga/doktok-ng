"""Host-script tests for the DRP drill verification (#755).

The shared comparison in deploy/lib-drill-verify.sh is what turns "the restore ran" into
"everything came back" - exercised via bash subprocess like the log_event/anomaly-guard tests,
plus text assertions that both drill scripts run the REAL engine and record the drill sentinel.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _run(script: str) -> subprocess.CompletedProcess[str]:
    body = (
        "set -euo pipefail\n"
        f"source '{REPO_ROOT}/deploy/lib.sh'\n"
        f"source '{REPO_ROOT}/deploy/lib-drill-verify.sh'\n"
        f"{script}\n"
    )
    return subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
    )


def test_counts_match_equal_rows() -> None:
    proc = _run('drill_counts_match "12|179|40|1|0|4|1|120" "12|179|40|1|0|4|1|120"')
    assert proc.returncode == 0, proc.stderr


def test_counts_match_reports_the_labeled_diff() -> None:
    proc = _run('drill_counts_match "12|179|40|1|0|4|1|120" "12|170|40|1|0|4|1|120"')
    assert proc.returncode == 1
    assert "document_chunks" in proc.stderr
    assert "baseline=179" in proc.stderr and "candidate=170" in proc.stderr
    # untouched tables are not reported
    assert "documents " not in proc.stderr


def test_counts_match_empty_candidate_fails() -> None:
    proc = _run('drill_counts_match "12|179|40|1|0|4|1|120" ""')
    assert proc.returncode == 1


def test_no_risk_drill_uses_real_verification() -> None:
    text = (REPO_ROOT / "deploy" / "restore-drill.sh").read_text(encoding="utf-8")
    assert "lib-drill-verify.sh" in text
    assert "DOKTOK_COMPOSE_FILES" in text
    assert "drill_counts_match" in text
    assert "drill_verify_hashes" in text
    assert "--stanza=doktok restore --delta" in text  # throwaway pgBackRest restore
    assert "write_status drill" in text and "drill_pass" in text


def test_dev_drill_uses_the_real_engine_and_writes_the_sentinel() -> None:
    text = (REPO_ROOT / "deploy" / "restore-drill-dev.sh").read_text(encoding="utf-8")
    assert "./deploy/backup.sh" in text
    assert "./deploy/restore.sh" in text
    assert "pitr_target" in text  # PITR, never "latest" for the pg leg
    assert "drill_counts_match" in text and "drill_verify_hashes" in text
    assert "write_status drill" in text and "drill_pass" in text and "drill_fail" in text
    # the old pg_dump/tar engine is gone
    assert "pg_dump" not in text
