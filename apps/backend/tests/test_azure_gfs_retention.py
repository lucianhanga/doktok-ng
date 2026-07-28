"""Host-script tests for the offsite retention v2 (GFS, #766).

The naming scheme is the contract: <leg>-repo-<class>-<ts>-<fp12>.tar.gz drives dedup
(fingerprint compare), promotion (period keys), and pruning (keep-counts per class). Exercised
via bash subprocess for name_parts/_period_key, plus text assertions on the script shape.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = (REPO_ROOT / "deploy" / "azure-sync.sh").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _fn(call: str) -> subprocess.CompletedProcess[str]:
    body = (
        "set -euo pipefail\n"
        f"source '{REPO_ROOT}/deploy/azure-sync.sh' >/dev/null 2>&1 || true\n"
        f"{call}\n"
    )
    # sourcing runs main; instead extract the functions we test by eval'ing only their defs
    return subprocess.run(
        ["bash", "-c", body], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}
    )


def _defs() -> str:
    """Extract just the helper function definitions (no main body) from the script."""
    import re

    parts = []
    names = ("_fp12", "name_parts", "_period_key", "keep_for", "tier_for", "container_for")
    for name in names + ("newest_blob",):
        m = re.search(rf"^{name}\(\) {{(.*?)^}}\n", SCRIPT, re.M | re.S)
        assert m, f"{name} not found"
        parts.append(m.group(0))
    return "\n".join(parts)


def _call(call: str) -> subprocess.CompletedProcess[str]:
    env = (
        "CONTAINER_SHORT=short CONTAINER_LTS=lts "
        "DOKTOK_AZURE_ACCOUNT=x DOKTOK_AZURE_CONTAINER=short"
    )
    body = f"{_defs()}\n{env}\n{call}\n"
    return subprocess.run(
        ["bash", "-c", body], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}
    )


def test_name_parts_parses_the_scheme() -> None:
    proc = _call('name_parts "pg-repo-weekly-20260728-103249-a91f3c02e1ab.tar.gz"')
    assert proc.returncode == 0
    assert proc.stdout.strip() == "weekly 20260728-103249 a91f3c02e1ab"


def test_newest_blob_sorts_by_timestamp_not_class_name() -> None:
    # daily < hourly < monthly < weekly < yearly lexically; the yearly blob here is OLDER than the
    # hourly one - naive name sort picks the yearly (wrong), newest_blob must pick the hourly.
    proc = _call(
        'printf "%s\\n" '
        '"files-repo-yearly-20260728-010000-aaaaaaaaaaaa.tar.gz" '
        '"files-repo-hourly-20260728-120000-bbbbbbbbbbbb.tar.gz" '
        '"files-repo-daily-20260728-030000-cccccccccccc.tar.gz" | newest_blob'
    )
    assert proc.stdout.strip() == "files-repo-hourly-20260728-120000-bbbbbbbbbbbb.tar.gz"


def test_period_keys() -> None:
    assert _call('_period_key daily 20260728').stdout.strip() == "20260728"
    assert _call('_period_key monthly 20260728').stdout.strip() == "202607"
    assert _call('_period_key yearly 20260728').stdout.strip() == "2026"
    # same ISO week for Monday and Sunday (weeks-since-epoch bucket)
    mon = _call('_period_key weekly 20260727').stdout.strip()
    sun = _call('_period_key weekly 20260802').stdout.strip()
    assert mon == sun
    nxt = _call('_period_key weekly 20260803').stdout.strip()
    assert int(nxt) == int(mon) + 1


def test_class_to_tier_and_container_mapping() -> None:
    assert _call('tier_for hourly').stdout.strip() == "Hot"
    assert _call('tier_for weekly').stdout.strip() == "Cool"
    assert _call('tier_for yearly').stdout.strip() == "Archive"
    assert _call('container_for hourly').stdout.strip() == "short"
    assert _call('container_for daily').stdout.strip() == "short"
    assert _call('container_for weekly').stdout.strip() == "lts"
    assert _call('container_for yearly').stdout.strip() == "lts"


def test_gfs_keep_counts() -> None:
    assert _call('keep_for hourly').stdout.strip() == "24"
    assert _call('keep_for daily').stdout.strip() == "7"
    assert _call('keep_for weekly').stdout.strip() == "4"
    assert _call('keep_for monthly').stdout.strip() == "11"
    assert _call('keep_for yearly').stdout.strip() == "1"


def test_script_shape() -> None:
    assert "DOKTOK_AZURE_CONTAINER_LTS" in SCRIPT
    assert "copy start" in SCRIPT and "--requires-sync" not in SCRIPT  # >256MB copies are async
    # uploads land in the cadence-driven BASE class; promotion to that class is skipped
    assert 'DOKTOK_GFS_BASE_CLASS:-hourly' in SCRIPT
    assert '[ "$cls" = "$BASE_CLASS" ] && continue' in SCRIPT
    # files fingerprint is host-side path+size (write-once pipeline); the restic tree id embeds
    # directory mtimes (refreshed by staging) and is useless as a content key
    assert "stat -f '%N %z'" in SCRIPT and "stat -c '%n %s'" in SCRIPT
    assert "pgbackrest --stanza=doktok info --output=json" in SCRIPT  # pg fingerprint
    # WORM is container-level (Terraform), not per-blob
    assert "immutability-policy set" not in SCRIPT
    assert "az storage blob sync" not in SCRIPT
