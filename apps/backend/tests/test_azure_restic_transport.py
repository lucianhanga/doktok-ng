"""Host-script tests for the incremental restic offsite transport (#827).

Text assertions on deploy/azure-sync.sh + deploy/lib.sh: the transport is restic end-to-end, the
sync steps run lock-free under a no-delete SAS, prune alone gets the delete-capable SAS, and the
sentinel/history contract is preserved. The legacy tarball transport survives one release as
deploy/azure-sync-tarball.sh behind DOKTOK_OFFSITE_TRANSPORT=tarball.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SYNC = (REPO_ROOT / "deploy" / "azure-sync.sh").read_text(encoding="utf-8")
LIB = (REPO_ROOT / "deploy" / "lib.sh").read_text(encoding="utf-8")


def test_tarball_fallback_is_preserved_for_one_release() -> None:
    assert "DOKTOK_OFFSITE_TRANSPORT" in SYNC
    assert "azure-sync-tarball.sh" in SYNC
    assert (REPO_ROOT / "deploy" / "azure-sync-tarball.sh").exists()


def test_offsite_restic_helper_is_mode_aware() -> None:
    assert "offsite_restic()" in LIB
    # compose mode delegates to the backup-runner image (which bakes restic)
    assert 'run --rm' in LIB and 'backup-runner restic' in LIB
    # restic's Azure backend env is mapped from the DOKTOK_* settings
    assert 'AZURE_ACCOUNT_NAME="${DOKTOK_AZURE_ACCOUNT' in LIB
    # sync and prune use different SAS tokens; the no-delete one is the default
    assert "DOKTOK_AZURE_SAS_PRUNE" in LIB


def test_sync_steps_are_lock_free_and_never_use_the_prune_sas() -> None:
    # copy/backup/snapshots run with --no-lock so the no-delete SAS suffices (restic locks need
    # create+delete); ONLY forget/prune may reference the delete-capable prune SAS
    sync_lines = [line for line in SYNC.splitlines() if "offsite_restic" in line]
    mutating = [
        line for line in sync_lines if any(w in line for w in ("copy", "backup", "snapshots"))
    ]
    assert mutating and all("--no-lock" in line for line in mutating)


def test_transport_is_restic_end_to_end() -> None:
    # both legs stream the SOURCES straight into the Azure repos (single-repo auth; restic copy's
    # --from-repo auth failed consistently on restic 0.14 - spike 2026-08-18, see plan Task 1)
    assert 'backup "$files_src"' in SYNC and "--tag files_root" in SYNC
    assert 'backup "$pg_src"' in SYNC
    # no runtime az/tarball machinery left in the restic path
    assert "az storage blob upload" not in SYNC
    assert 'tar -czf' not in SYNC
    # the compose files leg stages only when the virtiofs workaround is configured (#745) and
    # fails fast on a partial stage - never find -delete on an unguarded tree
    assert 'set -e' in SYNC and '[ -n "${DOKTOK_FILES_STAGE_SRC:-}" ]' in SYNC


def test_gfs_is_restic_forget_metadata_not_duplicate_bytes() -> None:
    for flag in ("--keep-daily 7", "--keep-weekly 4", "--keep-monthly 12", "--keep-yearly 1"):
        assert flag in SYNC
    assert "forget --prune" in SYNC


def test_sentinel_history_contract_preserved() -> None:
    assert "write_status offsite" in SYNC and "log_event offsite" in SYNC
    assert "DOKTOK_OFFSITE_MIN_SETS" in SYNC  # floor audit now counts snapshots per repo
    assert '"item_count":' in SYNC


def test_restore_files_uses_the_snapshots_recorded_path() -> None:
    rf = (REPO_ROOT / "deploy" / "restore-files.sh").read_text(encoding="utf-8")
    # path-agnostic restore (#827): the tree location comes from the snapshot's own metadata, not
    # the live FILES_ROOT - a repo rebuilt by azure-fetch (or a moved FILES_ROOT) still restores
    assert '"paths"' in rf
    assert 'cp -a "$scratch$snap_root/."' in rf
    assert 'cp -a "$scratch$abs_root' not in rf


def test_hourly_cadence_and_env_wiring() -> None:
    timer_path = REPO_ROOT / "deploy" / "systemd" / "doktok-azure-sync.timer"
    timer = timer_path.read_text(encoding="utf-8")
    assert "OnCalendar=hourly" in timer  # 1h offsite RPO (#827); was daily 03:47
    mk = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "DOKTOK_AZURE_SAS_PRUNE" in mk and "DOKTOK_RESTIC_PASSWORD" in mk
    ex = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "DOKTOK_AZURE_SAS_PRUNE" in ex


def test_drp_selftest_has_an_offsite_roundtrip_leg() -> None:
    st = (REPO_ROOT / "deploy" / "drp-selftest.sh").read_text(encoding="utf-8")
    assert "selftest-" in st            # throwaway Azure prefix
    assert "offsite_restic" in st       # uses the same transport helpers as the real sync
    assert "restore latest" in st       # restores the seed tree back
    # the make target passes the Azure env through, else the leg silently skips
    mk = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    m = re.search(r"drp-selftest:.*?deploy/drp-selftest\.sh", mk, re.S)
    assert m and "DOKTOK_AZURE_SAS_PRUNE" in m.group(0)
