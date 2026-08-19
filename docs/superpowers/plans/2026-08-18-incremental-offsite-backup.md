# Incremental Offsite Backup (#827) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the whole-repo-tarball Azure transport with incremental restic repos on Azure Blob (`/files`, `/pg`), hourly cadence, GFS via `restic forget --prune`, access-layer immutability (no-delete sync SAS + soft-delete) instead of container WORM.

**Architecture:** Two restic repos on Azure Blob (`azure:<container>:/files`, `azure:<container>:/pg`). Hourly: `restic copy` new files snapshots → `restic backup` of the pgBackRest repo dir → `forget --prune` both (keep-daily 7 / weekly 4 / monthly 12 / yearly 1). `azure-sync.sh` is rewritten thin; the tarball version survives one release as `azure-sync-tarball.sh` behind `DOKTOK_OFFSITE_TRANSPORT=tarball`. Sentinel/history contract (`backups/status/offsite.json`, `history.jsonl`) is byte-preserved so the DRP panel + watchdog never notice.

**Tech Stack:** bash (3.2-safe, macOS), restic (Azure Blob backend), pgBackRest, az CLI (provisioning only), Terraform (infra), pytest text-contract tests (repo convention for deploy/*.sh).

**Spec:** `docs/superpowers/specs/2026-08-18-incremental-offsite-backup-design.md` (approved). Issue: #827.

## Global Constraints

- **Repo workflow (user-mandated):** work on branch `fix/offsite-incremental-restic` linked to issue #827 (GraphQL `createLinkedBranch`), Roadmap project Status = In Progress; **every commit needs explicit user approval** — the commit steps below are batched per task and only run after the user says ok. On merge/close set Status = Done.
- **Contract preservation:** `write_status offsite <true|false> "<detail>"` and `log_event offsite success true "<detail>" "\"item_count\":${n}"` must keep being called exactly once per sync run (fields `leg/ok/last_run_at/detail` are read by `apps/backend/doktok_api/routers/settings.py` DRP endpoint; `/metrics` gauges scrape them).
- **Secrets:** SAS tokens and `DOKTOK_RESTIC_PASSWORD` never appear in logs, history details, or test fixtures. History `detail` free-text rule from `lib.sh` applies (no command lines/stderr).
- **bash 3.2-safe** (macOS): no assoc arrays, no `mapfile`; follow existing deploy/*.sh style.
- **Mode-awareness:** all restic invocations go through the `offsite_restic` helper (Task 3) — host mode = local `restic`; compose mode = `docker compose run --rm backup-runner restic` (the runner image bakes `deploy/` at build time ⇒ rebuild it after changing deploy/: `docker compose -f docker-compose.yml -f docker-compose.dev.yml build backup-runner`, and prod gets it via `deploy-to-box.sh` rebuild).
- **TDD per repo convention:** contract tests are pytest text assertions under `apps/backend/tests/`; write/rewrite the test first, watch it fail, then implement.
- **`make check` green at the end** (lint + typecheck + test + arch + js).
- restic version floor: any build with `copy` + `--no-lock` (restic ≥ 0.14; Debian bookworm ships 0.15.x, verified in Task 1 spike).

---

### Task 1: Live spike — prove restic-on-Azure with a no-delete SAS

**Files:** none (throwaway Azure container; repo untouched).

**Purpose:** the whole design hinges on three assumptions — verify them live before writing repo code: (a) restic's Azure backend works with `AZURE_ACCOUNT_SAS`, (b) `init`/`backup`/`copy`/`snapshots` succeed with a SAS **without delete permission** when `--no-lock` is used (restic otherwise creates+deletes lock files, which needs delete), (c) `forget --prune` works with a delete-capable SAS (with locking).

- [ ] **Step 1: create throwaway container + two SAS tokens (24 h expiry)**

```bash
export DOKTOK_AZURE_ACCOUNT="$(grep '^DOKTOK_AZURE_ACCOUNT=' .env | cut -d= -f2-)"
AZ_KEY="$(az storage account keys list --account-name "$DOKTOK_AZURE_ACCOUNT" --query '[0].value' -o tsv)"
az storage container create --name "restic-spike" --account-name "$DOKTOK_AZURE_ACCOUNT" --account-key "$AZ_KEY" -o none
EXP="$(date -u -v+24H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '+24 hours' +%Y-%m-%dT%H:%M:%SZ)"
SAS_NODEL="$(az storage container generate-sas --name restic-spike --account-name "$DOKTOK_AZURE_ACCOUNT" --account-key "$AZ_KEY" --permissions rwcl --expiry "$EXP" --https-only -o tsv)"
SAS_FULL="$(az storage container generate-sas --name restic-spike --account-name "$DOKTOK_AZURE_ACCOUNT" --account-key "$AZ_KEY" --permissions rwcld --expiry "$EXP" --https-only -o tsv)"
```

- [ ] **Step 2: init + backup + snapshots with the no-delete SAS and `--no-lock`**

```bash
export AZURE_ACCOUNT_NAME="$DOKTOK_AZURE_ACCOUNT" AZURE_ACCOUNT_SAS="$SAS_NODEL"
export RESTIC_REPOSITORY="azure:restic-spike:/files" RESTIC_PASSWORD="spike-test-pw"
restic init --no-lock            # expected: created repo
mkdir -p /tmp/spike-data && echo hello > /tmp/spike-data/a.txt
restic backup /tmp/spike-data --no-lock   # expected: snapshot created, no delete-permission error
restic snapshots --no-lock                # expected: 1 snapshot
```

Expected: all three succeed. If `init` fails on lock creation, retry `restic init` (init may not accept `--no-lock` on older builds; bookworm 0.15 does accept it via copy/backup/snapshots — if init alone needs delete, run init with SAS_FULL and everything else with SAS_NODEL; record the outcome).

- [ ] **Step 3: copy + forget/prune split**

```bash
restic -r azure:restic-spike:/files2 init --no-lock
restic -r azure:restic-spike:/files2 --no-lock copy --from-repo azure:restic-spike:/files \
  --from-password-command 'echo spike-test-pw'    # expected: 1 snapshot copied
export AZURE_ACCOUNT_SAS="$SAS_FULL"
restic -r azure:restic-spike:/files forget --prune --keep-daily 7   # expected: succeeds WITH lock
```

- [ ] **Step 4: teardown + record**

```bash
az storage container delete --name restic-spike --account-name "$DOKTOK_AZURE_ACCOUNT" --account-key "$AZ_KEY" -o none
rm -rf /tmp/spike-data
```

Record go/no-go per assumption in the PR body later. If (b) fails even with `--no-lock`: fall back to a single delete-capable SAS + soft-delete only, and drop the two-SAS invariant from Tasks 3–6 (note it in the ADR).

**Spike outcome (recorded 2026-08-18, dev box, container `doktok-backups`, restic 0.14.0 in the backup-runner image):**
- ✅ SAS auth (`AZURE_ACCOUNT_SAS`), `init`, `backup`, `snapshots` work; backup exit code 0.
- ✅ restic + container WORM confirmed incompatible (lock-removal 409 retries ~60 s/run; a stale exclusive lock blocked `forget` entirely) → the WORM removal in Task 6 is required, not optional.
- ⚠️ restic 0.14 creates+removes a lock even with `--no-lock`; with a no-delete SAS the removal fails non-fatally → the prune step sweeps orphan locks via `unlock --remove-all` under the delete-capable SAS (sync and prune are sequential within one run).
- ❌ `restic copy --from-repo` auth failed consistently ("wrong password or no key found", also via `RESTIC_FROM_PASSWORD_COMMAND`, on fresh repos) → **pivoted**: files leg = direct `backup` of the (staged) files tree; fetch = `restore` + local repo rebuild. Single-repo auth everywhere.
- Leftovers: test prefixes `spike-161120`, `spike2-161120`, `spike3`, `spike4` in the short container (KBs; WORM-locked 2 days) — delete on/after 2026-08-20 or let the lifecycle rule expire them.

---

### Task 2: Preserve the tarball transport as the one-release rollback

**Files:**
- Move: `deploy/azure-sync.sh` → `deploy/azure-sync-tarball.sh` (git mv, content unchanged)
- Create: `deploy/azure-sync.sh` (dispatch shim only, replaced for real in Task 4)
- Test: `apps/backend/tests/test_azure_gfs_retention.py` (repoint at tarball script)

**Interfaces:**
- Consumes: nothing.
- Produces: `deploy/azure-sync-tarball.sh` keeping every existing function (`current_fp`, `keep_for`, `name_parts`, …) verbatim; `azure-sync.sh` shim honoring `DOKTOK_OFFSITE_TRANSPORT` (`restic` default, `tarball` → exec the old script).

- [ ] **Step 1: repoint the existing GFS tests (they guard the tarball fallback until it is removed)**

In `apps/backend/tests/test_azure_gfs_retention.py` change:

```python
SCRIPT = (REPO_ROOT / "deploy" / "azure-sync.sh").read_text(encoding="utf-8")
```

to:

```python
SCRIPT = (REPO_ROOT / "deploy" / "azure-sync-tarball.sh").read_text(encoding="utf-8")
```

- [ ] **Step 2: add the shim contract test — watch it fail**

New file `apps/backend/tests/test_azure_restic_transport.py`:

```python
"""Host-script tests for the incremental restic offsite transport (#827).

Text assertions on deploy/azure-sync.sh + deploy/lib.sh: the transport is restic end-to-end, the
sync steps run lock-free under a no-delete SAS, prune alone gets the delete-capable SAS, and the
sentinel/history contract is preserved. The legacy tarball transport survives one release as
deploy/azure-sync-tarball.sh behind DOKTOK_OFFSITE_TRANSPORT=tarball.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SYNC = (REPO_ROOT / "deploy" / "azure-sync.sh").read_text(encoding="utf-8")
LIB = (REPO_ROOT / "deploy" / "lib.sh").read_text(encoding="utf-8")


def test_tarball_fallback_is_preserved_for_one_release() -> None:
    assert "DOKTOK_OFFSITE_TRANSPORT" in SYNC
    assert "azure-sync-tarball.sh" in SYNC
    assert (REPO_ROOT / "deploy" / "azure-sync-tarball.sh").exists()
```

Run: `uv run pytest apps/backend/tests/test_azure_restic_transport.py -q`
Expected: FAIL (`DOKTOK_OFFSITE_TRANSPORT` not in SYNC).

- [ ] **Step 3: move + shim**

```bash
git mv deploy/azure-sync.sh deploy/azure-sync-tarball.sh
```

New `deploy/azure-sync.sh` (temporary shim; Task 4 writes the real body below this dispatch):

```bash
#!/usr/bin/env bash
#
# Offsite sync entry point (#827). Default transport is the incremental restic design; the pre-#827
# whole-repo tarball transport survives one release behind DOKTOK_OFFSITE_TRANSPORT=tarball.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "${DOKTOK_OFFSITE_TRANSPORT:-restic}" = "tarball" ]; then
    exec ./deploy/azure-sync-tarball.sh "$@"
fi

echo "restic transport not implemented yet (task 4)" >&2
exit 1
```

- [ ] **Step 4: run tests**

Run: `uv run pytest apps/backend/tests/test_azure_restic_transport.py apps/backend/tests/test_azure_gfs_retention.py -q`
Expected: PASS (the repointed GFS tests still pass against the moved script).

- [ ] **Step 5: commit (with user approval)**

```bash
git add deploy/azure-sync.sh deploy/azure-sync-tarball.sh apps/backend/tests/
git commit -m "refactor(deploy): preserve tarball offsite transport as one-release rollback (#827)"
```

---

### Task 3: `offsite_restic` helper in lib.sh (mode-aware runner + Azure env)

**Files:**
- Modify: `deploy/lib.sh` (append at end)
- Test: `apps/backend/tests/test_azure_restic_transport.py` (extend)

**Interfaces:**
- Consumes: existing lib.sh conventions; `compose` array + `$mode` set by the calling script (same dynamic-scoping pattern as `backup.sh`).
- Produces: `offsite_restic <restic-args...>` — runs restic against the Azure repo; host mode: local `restic "$@"`; compose mode: `"${compose[@]}" run --rm -e RESTIC_REPOSITORY -e RESTIC_PASSWORD -e AZURE_ACCOUNT_NAME -e AZURE_ACCOUNT_SAS backup-runner restic "$@"`. Also `offsite_snapshot_count` — prints the Azure repo's snapshot count (host-side python3 parse; the runner has no python).

- [ ] **Step 1: extend the failing contract test**

Append to `test_azure_restic_transport.py`:

```python
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
    sync_lines = [l for l in SYNC.splitlines() if "offsite_restic" in l]
    mutating = [l for l in sync_lines if any(w in l for w in ("copy", "backup", "snapshots"))]
    assert mutating and all("--no-lock" in l for l in mutating)
```

Run: `uv run pytest apps/backend/tests/test_azure_restic_transport.py -q` → FAIL.

- [ ] **Step 2: implement the helper — append to `deploy/lib.sh`**

```bash
# --- Offsite (Azure) restic transport (#827) -----------------------------------------------

# offsite_restic <restic-args...> - run restic for the OFFSITE copy, mode-aware like backup.sh:
# host mode runs the local restic; compose mode runs restic in the backup-runner image (it bakes
# restic + sees $BACKUP_DIR at /backups). The caller exports RESTIC_REPOSITORY / RESTIC_PASSWORD /
# AZURE_ACCOUNT_NAME / AZURE_ACCOUNT_SAS first; compose mode passes them through by name.
# Secrets travel via env only - never on the command line (ps-visible) or in logs.
offsite_restic() {
    if [ "${mode:-host}" = "compose" ]; then
        "${compose[@]}" run --rm \
            -e RESTIC_REPOSITORY -e RESTIC_PASSWORD \
            -e AZURE_ACCOUNT_NAME -e AZURE_ACCOUNT_SAS \
            backup-runner restic "$@"
    else
        restic "$@"
    fi
}

# offsite_azure_env <sync|prune> - map the DOKTOK_AZURE_* settings onto restic's Azure backend env.
# `sync` uses the NO-delete SAS (ransomware layer: the hourly writer cannot destroy history);
# `prune` alone uses the delete-capable, host-only SAS (forget/prune deletes packs + lock files).
offsite_azure_env() {
    export AZURE_ACCOUNT_NAME="${DOKTOK_AZURE_ACCOUNT:?set DOKTOK_AZURE_ACCOUNT}"
    if [ "$1" = "prune" ]; then
        export AZURE_ACCOUNT_SAS="${DOKTOK_AZURE_SAS_PRUNE:?set DOKTOK_AZURE_SAS_PRUNE (host-only, delete-capable)}"
    else
        export AZURE_ACCOUNT_SAS="${DOKTOK_AZURE_SAS:?set DOKTOK_AZURE_SAS}"
    fi
}

# offsite_snapshot_count - snapshot count of $RESTIC_REPOSITORY (host-side python3 parse; the
# backup-runner image has no python). Prints 0 on any failure (audit only, never blocks a sync).
offsite_snapshot_count() {
    offsite_restic snapshots --no-lock --json 2>/dev/null \
        | python3 -c 'import sys, json; print(len(json.load(sys.stdin)))' 2>/dev/null || printf '0'
}
```

- [ ] **Step 3: run tests**

Run: `uv run pytest apps/backend/tests/test_azure_restic_transport.py -q`
Expected: helper test PASS; the `SYNC`-based test still FAILS (azure-sync.sh is the shim) — that test is completed in Task 4.

- [ ] **Step 4: commit (with user approval)** — `chore(deploy): mode-aware offsite restic helper + Azure env mapping (#827)`

---

### Task 4: Rewrite azure-sync.sh — the incremental transport

**Files:**
- Modify: `deploy/azure-sync.sh` (replace the shim body after the dispatch)
- Test: `apps/backend/tests/test_azure_restic_transport.py` (extend)

**Interfaces:**
- Consumes: `offsite_restic`, `offsite_azure_env`, `offsite_snapshot_count` (Task 3); `write_status`, `log_event`, `require`, `warn/ok/err` from lib.sh; `$BACKUP_DIR`, `$FILES_REPO` from lib.sh.
- Produces: hourly sync behaviour with the SAME sentinel/history contract as before. Env: `DOKTOK_AZURE_ACCOUNT`, `DOKTOK_AZURE_CONTAINER`, `DOKTOK_AZURE_SAS` (no delete), `DOKTOK_AZURE_SAS_PRUNE` (delete), `DOKTOK_RESTIC_PASSWORD`, `DOKTOK_OFFSITE_MIN_SETS` (default 3, now counts snapshots per Azure repo), `DOKTOK_COMPOSE_FILES`/`DOKTOK_COMPOSE_ENV_FILE`/`DOKTOK_DEPLOY_MODE` as in backup.sh.

- [ ] **Step 1: extend the failing contract test**

Append to `test_azure_restic_transport.py`:

```python
def test_transport_is_restic_end_to_end() -> None:
    # both legs stream the SOURCES straight into the Azure repos (single-repo auth; restic copy's
    # --from-repo auth failed consistently on restic 0.14 - spike 2026-08-18, see plan Task 1)
    assert 'backup "$files_src"' in SYNC and "--tag files_root" in SYNC
    assert 'backup "$pg_src"' in SYNC
    # no runtime az/tarball machinery left in the restic path
    assert "az storage blob upload" not in SYNC
    assert 'tar -czf' not in SYNC


def test_gfs_is_restic_forget_metadata_not_duplicate_bytes() -> None:
    for flag in ("--keep-daily 7", "--keep-weekly 4", "--keep-monthly 12", "--keep-yearly 1"):
        assert flag in SYNC
    assert "forget --prune" in SYNC


def test_sentinel_history_contract_preserved() -> None:
    assert "write_status offsite" in SYNC and "log_event offsite" in SYNC
    assert "DOKTOK_OFFSITE_MIN_SETS" in SYNC  # floor audit now counts snapshots per repo
    assert '"item_count":' in SYNC
```

Run → FAIL.

- [ ] **Step 2: write the new azure-sync.sh body**

Replace the shim's tail (`echo "restic transport not implemented yet"...`) — keep the header + tarball dispatch — with:

```bash
# Incremental restic transport (#827). Two Azure restic repos (created by azure-provision.sh):
#   azure:<container>:/files  <- restic backup of the files tree (same source => same chunk dedup)
#   azure:<container>:/pg     <- restic backup of the pgBackRest repo dir (write-once => perfect
#                                dedup; restore stays two-stage via azure-fetch.sh)
# GFS retention is restic forget metadata over shared chunks - no duplicate bytes (#766 tarballs
# kept up to 23 full copies per leg). Hourly cadence => 1h offsite RPO (pg WAL rides inside the
# pg repo; local RPOs are unchanged: WAL 60s, files 15min).
source deploy/lib.sh

: "${DOKTOK_AZURE_CONTAINER:?set DOKTOK_AZURE_CONTAINER}"
: "${DOKTOK_RESTIC_PASSWORD:?set DOKTOK_RESTIC_PASSWORD}"
min_sets="${DOKTOK_OFFSITE_MIN_SETS:-3}"
dry_run="${1:-}"

# fail_sync + the ERR trap come FIRST so every later failure (missing tools, missing repos, failed
# legs) writes the failure sentinel - the DRP contract holds on every exit path.
fail_sync() {  # local override keeps the legacy failure contract (sentinel + stderr + exit 1)
    write_status offsite false "azure sync failed: $1"
    err "azure sync FAILED: $1"
    exit 1
}
trap 'fail_sync "unexpected error"' ERR

mode="${DOKTOK_DEPLOY_MODE:-host}"
COMPOSE_FILES="${DOKTOK_COMPOSE_FILES:-docker-compose.prod.yml}"
COMPOSE_ENV_FILE="${DOKTOK_COMPOSE_ENV_FILE:-.env.production}"
compose=(docker compose)
for f in ${COMPOSE_FILES//,/ }; do compose+=(-f "$f"); done
compose+=(--env-file "$COMPOSE_ENV_FILE")

# Host mode needs a local restic; compose mode uses the backup-runner's (baked into the image).
# NOTE: do NOT write `require restic || true` - require() exits the whole script on failure.
if [ "$mode" != "compose" ]; then require restic; fi

[ -d "$FILES_REPO" ] || fail_sync "no files repo at $FILES_REPO - run a backup first"
[ -d "$BACKUP_DIR/pg" ] || fail_sync "no pg repo at $BACKUP_DIR/pg - run a backup first"

# restic sees the pg repo by its IN-RUNNER path in compose mode (/backups), host path otherwise.
if [ "$mode" = "compose" ]; then
    pg_src="/backups/pg"
else
    pg_src="$BACKUP_DIR/pg"
fi
export RESTIC_PASSWORD="$DOKTOK_RESTIC_PASSWORD"

# --- sync both legs (no-delete SAS; interruption-safe: content-addressed => rerun) -------------
# NOTE on locks: restic 0.14 creates+removes a lock even with --no-lock (spike 2026-08-18). With
# the no-delete sync SAS the removal fails (non-fatal, exit stays 0) and orphan locks pile up;
# the prune step below sweeps them (unlock --remove-all) under the delete-capable SAS.
# NOTE on copy: `restic copy --from-repo` would spare re-reading the files tree, but its
# cross-repo password auth failed consistently in the spike (restic 0.14) - direct backup keeps
# every leg on single-repo auth. Revisit as an optimization at scale.
offsite_azure_env sync
export RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/files"
echo "files: backup -> $RESTIC_REPOSITORY"
if [ "$dry_run" != "--dry-run" ]; then
    if [ "$mode" = "compose" ]; then
        files_src="/data/files"
        # Reuse backup-files.sh's virtiofs staging (O_NOATIME workaround, #745): the runner's
        # service env already maps DOKTOK_FILES_STAGE_SRC=/host/files + DOKTOK_FILES_ROOT=/data/files.
        "${compose[@]}" run --rm -e AZURE_ACCOUNT_NAME -e AZURE_ACCOUNT_SAS \
            -e RESTIC_REPOSITORY -e RESTIC_PASSWORD backup-runner bash -c '
                find "$DOKTOK_FILES_ROOT" -mindepth 1 -delete
                cp -a "$DOKTOK_FILES_STAGE_SRC/." "$DOKTOK_FILES_ROOT/"
                restic backup "$DOKTOK_FILES_ROOT" --tag files_root --host doktok --no-lock \
                    --exclude ".DS_Store" --exclude "Thumbs.db" --exclude "desktop.ini" \
                    --exclude ".localized"
            ' || fail_sync "files backup to Azure failed"
    else
        files_src="$FILES_ROOT"
        offsite_restic backup "$files_src" --tag files_root --host doktok --no-lock \
            --exclude ".DS_Store" --exclude "Thumbs.db" --exclude "desktop.ini" --exclude ".localized" \
            || fail_sync "files backup to Azure failed"
    fi
fi

export RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/pg"
echo "pg: backup $pg_src -> $RESTIC_REPOSITORY"
[ "$dry_run" = "--dry-run" ] || \
    offsite_restic backup "$pg_src" --no-lock --exclude log --exclude lock \
        || fail_sync "pg repo backup to Azure failed"

# --- GFS retention: snapshot metadata, not duplicate bytes (the only delete-capable step) -------
if [ "$dry_run" != "--dry-run" ]; then
    offsite_azure_env prune
    for prefix in files pg; do
        export RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/${prefix}"
        # Sweep the sync steps' orphan locks first (sync SAS can't delete); safe because sync and
        # prune are sequential within ONE run.
        offsite_restic unlock --remove-all 2>/dev/null || true
        offsite_restic forget --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --keep-yearly 1 \
            || warn "prune failed for $prefix (retry next run)"
    done
    offsite_azure_env sync
fi

# --- audit + sentinel (contract unchanged) -------------------------------------------------------
files_sets="$(RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/files" offsite_snapshot_count)"
pg_sets="$(RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/pg" offsite_snapshot_count)"
detail="offsite snapshots pg=${pg_sets} files=${files_sets} (hourly restic; content current)"
if [ "$dry_run" = "--dry-run" ]; then
    warn "dry-run - would backup the files tree + $pg_src, then prune 7d/4w/12m/1y"
elif [ "${pg_sets:-0}" -lt "$min_sets" ] || [ "${files_sets:-0}" -lt "$min_sets" ]; then
    write_status offsite true "WARN: fewer than ${min_sets} offsite snapshots - ${detail}"
    warn "offsite history below the minimum (${min_sets}); it builds up over the next runs"
else
    write_status offsite true "$detail"
fi
[ "$dry_run" = "--dry-run" ] || log_event offsite success true "$detail" "\"item_count\":${pg_sets}"
ok "offsite sync complete ($detail)"
```

Note for the implementer: `fail_sync` is intentionally redefined locally (the legacy script defined its own too); if lib.sh later grows one, drop this override. `require restic || true` stays soft because compose mode needs no host restic.

- [ ] **Step 3: run tests**

Run: `uv run pytest apps/backend/tests/test_azure_restic_transport.py apps/backend/tests/test_azure_gfs_retention.py -q`
Expected: PASS (all).

- [ ] **Step 4: live smoke on the dev box (needs the provisioned repos — Task 9 inits them; if run before, init by hand once: `offsite_azure_env sync`-equivalent env + `restic -r azure:…:/files init --no-lock` and `/pg`)**

Run: `DOKTOK_OFFSITE_TRANSPORT=restic make dev-azure-sync` (add the Makefile passthrough from Task 7 first if testing via make)
Expected: `offsite sync complete (offsite snapshots pg=1 files=N ...)`; `cat backups/status/offsite.json` shows `"ok":true`.

- [ ] **Step 5: commit (with user approval)** — `feat(deploy): incremental restic offsite transport (#827)`

---

### Task 5: azure-fetch.sh — restore from the Azure restic repos

**Files:**
- Modify: `deploy/azure-fetch.sh` (rewrite body; keep usage + staging contract)
- Modify: `deploy/restore-files.sh` (path-agnostic restore, Step 2b)
- Test: `apps/backend/tests/test_azure_offsite_sync.py` (replace the fetch assertions), `apps/backend/tests/test_azure_restic_transport.py` (restore-files assertion)

**Interfaces:**
- Consumes: Task 3 helpers; same env as Task 4.
- Produces: unchanged CLI `./deploy/azure-fetch.sh [staging-dir] [timestamp]`; staging layout still mirrors `$DOKTOK_BACKUP_DIR` (staging/files = restic repo, staging/pg = pgBackRest repo) so `DOKTOK_BACKUP_DIR=<staging> ./deploy/restore.sh …` keeps working.

- [ ] **Step 1: replace the fetch contract test (watch fail)**
In `apps/backend/tests/test_azure_offsite_sync.py`, replace `test_azure_fetch_downloads_unpacks_and_verifies` with:

```python
def test_azure_fetch_restores_from_the_restic_repos() -> None:
    fetch = (REPO_ROOT / "deploy" / "azure-fetch.sh").read_text(encoding="utf-8")
    # files leg: restore the tree from Azure, then rebuild a local restic repo from it (the
    # restore engine consumes staging/files as a repo; single-repo auth only - no restic copy)
    assert "restore latest" in fetch and "restic init" in fetch
    # pg leg: restic restore of the pg-repo snapshot, then pgBackRest runs against the result
    assert "--stanza=doktok info" in fetch  # fetched pgBackRest repo verified readable
    # the staging contract the restore engine consumes is unchanged
    assert "DOKTOK_BACKUP_DIR=${staging}" in fetch
    # no tarball machinery left
    assert "az storage blob download" not in fetch
```

Run: `uv run pytest apps/backend/tests/test_azure_offsite_sync.py -q` → FAIL (old assertions also still reference tarballs — the whole file is finalized in Task 6; for now also delete `test_sync_bundles_one_tarball_per_leg` and `test_sync_uploads_without_overwrite_and_audits_the_floor`, superseded by test_azure_restic_transport.py).

- [ ] **Step 2: rewrite azure-fetch.sh**

Keep header comment shape + usage; new body:

```bash
#!/usr/bin/env bash
#
# Fetch the offsite backup repos from Azure into a local staging dir (#359, restic transport #827).
# Rebuilds BOTH repos under the staging dir (chunk-deduped - only missing chunks cross the wire):
#   staging/files  restic repo   (restore latest tree from azure:/files, then init+backup to rebuild)
#   staging/pg     pgBackRest repo (via restic restore of the azure:/pg snapshot)
# The SAME deploy/restore.sh then restores from staging (point DOKTOK_BACKUP_DIR at it).
#
# Usage: ./deploy/azure-fetch.sh [staging-dir] [timestamp]
#   timestamp  yyyymmdd[-hhmmss]; selects the newest /pg snapshot at or before it (default: latest)
#
# Env: as azure-sync.sh (DOKTOK_AZURE_ACCOUNT/CONTAINER/SAS, DOKTOK_RESTIC_PASSWORD).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

: "${DOKTOK_AZURE_CONTAINER:?set DOKTOK_AZURE_CONTAINER}"
: "${DOKTOK_RESTIC_PASSWORD:?set DOKTOK_RESTIC_PASSWORD}"
trap 'err "azure fetch FAILED"; exit 1' ERR

staging="${1:-./backups.azure-restore}"
want_ts="${2:-}"
mode="${DOKTOK_DEPLOY_MODE:-host}"
COMPOSE_FILES="${DOKTOK_COMPOSE_FILES:-docker-compose.prod.yml}"
COMPOSE_ENV_FILE="${DOKTOK_COMPOSE_ENV_FILE:-.env.production}"
compose=(docker compose)
for f in ${COMPOSE_FILES//,/ }; do compose+=(-f "$f"); done
compose+=(--env-file "$COMPOSE_ENV_FILE")

offsite_azure_env sync
export RESTIC_PASSWORD="$DOKTOK_RESTIC_PASSWORD"
mkdir -p "$staging/files" "$staging/pg"
staging_abs="$(cd "$staging" && pwd)"
require python3  # the snapshot picker runs host-side in both modes (lib.sh relies on host python3 too)
if [ "$mode" != "compose" ]; then require restic; fi

# staging_restic <args...> - restic with the staging dir in scope: host mode runs the local
# restic; compose mode runs the runner with $staging_abs bind-mounted at /backups (callers then
# use /backups/... paths). Forwards the Azure env too, so the same helper serves the Azure
# restores AND the local repo rebuild.
staging_restic() {
    if [ "$mode" = "compose" ]; then
        "${compose[@]}" run --rm -v "$staging_abs:/backups" \
            -e RESTIC_REPOSITORY -e RESTIC_PASSWORD -e AZURE_ACCOUNT_NAME -e AZURE_ACCOUNT_SAS \
            backup-runner restic "$@"
    else
        restic "$@"
    fi
}

# Mode-specific path spaces: restic --target must be runner-visible in compose mode.
if [ "$mode" = "compose" ]; then
    tree_target="/backups/.filestree"; pgtree_target="/backups/.pgtree"; st_repo="/backups/files"
else
    tree_target="$staging_abs/.filestree"; pgtree_target="$staging_abs/.pgtree"; st_repo="$staging_abs/files"
fi

# Pick the /pg snapshot: newest at/before the requested ts (default: latest). IDs listed newest-last.
snap_json="$(RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/pg" offsite_restic snapshots --no-lock --json)"
snap_id="$(printf '%s' "$snap_json" | python3 -c '
import sys, json
want = sys.argv[1].replace("-", "")
if len(want) == 8:  # date-only means "up to end of that day"
    want += "235959"
snaps = sorted(json.load(sys.stdin), key=lambda s: s["time"])
pick = ""
for s in snaps:
    stamp = s["time"][:19].replace("-", "").replace("T", "").replace(":", "")
    if not want or stamp <= want:
        pick = s["id"]
print(pick)
' "$want_ts")"
[ -n "$snap_id" ] || { err "no /pg offsite snapshot at/before '${want_ts:-latest}'"; exit 1; }
echo "fetching offsite state (pg snapshot ${snap_id}) -> ${staging}"

# files leg: restore the latest tree from Azure, then rebuild a local restic repo from it (the
# restore engine consumes staging/files as a repo; single-repo auth only - restic copy's
# --from-repo auth proved unreliable on restic 0.14, spike 2026-08-18). The tree lands under
# <target><snapshot's recorded path>; read that path from the snapshot metadata instead of
# guessing depths.
files_path="$(RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/files" offsite_restic snapshots latest --no-lock --json \
    | sed -n 's/.*"paths":\["\([^"]*\)"\].*/\1/p')"
[ -n "$files_path" ] || { err "no /files offsite snapshot"; exit 1; }
RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/files" \
    staging_restic restore latest --no-lock --target "$tree_target"
tree_root="$staging_abs/.filestree$files_path"
[ -d "$tree_root" ] || { err "restored files tree missing at $tree_root"; exit 1; }
if [ "$mode" = "compose" ]; then
    st_tree="/backups/.filestree$files_path"
else
    st_tree="$tree_root"
fi
RESTIC_REPOSITORY="$st_repo" staging_restic init 2>/dev/null || true
RESTIC_REPOSITORY="$st_repo" staging_restic backup "$st_tree" --tag files_root --host doktok >/dev/null
rm -rf "$staging_abs/.filestree"
# pg leg: restore the snapshot; restic recreates the original absolute paths under the target, so
# locate the pg repo root (the dir holding backup/ + archive/) wherever it landed. -print -quit:
# first match, no SIGPIPE under pipefail.
RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/pg" \
    staging_restic restore "$snap_id" --no-lock --target "$pgtree_target"
pg_tree="$(find "$staging_abs/.pgtree" -type d -name archive -path '*/pg/*' -print -quit)"
[ -n "$pg_tree" ] || { err "restored pg snapshot has no pg archive dir"; exit 1; }
cp -a "$(dirname "$pg_tree")/" "$staging_abs/pg/"
rm -rf "$staging_abs/.pgtree"

# Verify both repos are readable (same best-effort checks as the tarball design; the compose
# array was already built above).
if "${compose[@]}" run --rm -v "$staging_abs:/backups" backup-runner \
        bash -c 'export RESTIC_REPOSITORY=/backups/files RESTIC_PASSWORD="$DOKTOK_RESTIC_PASSWORD"; restic snapshots >/dev/null' 2>/dev/null; then
    ok "restic repo readable"
else
    warn "restic verify skipped/failed - check by hand before restoring"
fi
if "${compose[@]}" run --rm -u postgres -v "$staging_abs/pg:/var/lib/doktok/pg" \
        --entrypoint pgbackrest db --stanza=doktok info >/dev/null 2>&1; then
    ok "pgBackRest repo readable"
else
    warn "pgBackRest verify skipped/failed - check by hand before restoring"
fi

ok "offsite state staged at ${staging}"
echo
echo "restore from it with:"
echo "  DOKTOK_BACKUP_DIR=${staging} ./deploy/restore.sh ./storage/files   # (+ optional PITR arg)"
echo "  (dev:  DOKTOK_BACKUP_DIR=${staging} make dev-restore FILES_TARGET=./storage/files)"
```

Note: `cp -a` into staging/pg mirrors what the tarball untar did; a same-filesystem `mv` is NOT used because the restored tree root varies by mode (absolute path baking).

- [ ] **Step 2b: restore-files.sh — restore by the snapshot's recorded path, not the live FILES_ROOT**

Review finding (plan-mandated defect, caught before it shipped): `restore-files.sh` recomputes the tree location from the LIVE `$FILES_ROOT` (`abs_root="$(cd "$FILES_ROOT" && pwd -P)"`, `cp -a "$scratch$abs_root/."`), which breaks for any repo whose snapshots were recorded at a different path — exactly what the Azure rebuild produces, and also the "FILES_ROOT moved since backup" DR case. Derive the path from the snapshot's own metadata instead (restic always records it):

In `deploy/restore-files.sh`, replace:

```bash
abs_root="$(cd "$FILES_ROOT" && pwd -P)"
echo "restoring snapshot $snapshot -> $target"
restic restore "$snapshot" --target "$scratch"
find "$target" -mindepth 1 -delete
cp -a "$scratch$abs_root/." "$target/"
```

with:

```bash
# The tree's location comes from the snapshot's own recorded path (#827): a repo rebuilt by
# azure-fetch, or a FILES_ROOT that moved since the snapshot, still restores. restic always
# records paths; single-root snapshots are the repo's invariant (backup-files.sh).
snap_root="$(restic snapshots "$snapshot" --json | sed -n 's/.*"paths":\["\([^"]*\)"\].*/\1/p')"
[ -n "$snap_root" ] || { err "cannot read the recorded path of snapshot $snapshot"; exit 1; }
echo "restoring snapshot $snapshot (path $snap_root) -> $target"
restic restore "$snapshot" --target "$scratch"
[ -d "$scratch$snap_root" ] || { err "restored tree missing at $scratch$snap_root"; exit 1; }
find "$target" -mindepth 1 -delete
cp -a "$scratch$snap_root/." "$target/"
```

(sed-parse, no new dependency: the runner image has no python3/jq; single-path arrays are the invariant.)

Append to `test_azure_restic_transport.py`:

```python
def test_restore_files_uses_the_snapshots_recorded_path() -> None:
    rf = (REPO_ROOT / "deploy" / "restore-files.sh").read_text(encoding="utf-8")
    # path-agnostic restore (#827): the tree location comes from the snapshot's own metadata, not
    # the live FILES_ROOT - a repo rebuilt by azure-fetch (or a moved FILES_ROOT) still restores
    assert '"paths"' in rf
    assert 'cp -a "$scratch$snap_root/."' in rf
    assert 'cp -a "$scratch$abs_root' not in rf
```

(Write this test in Step 1 together with the fetch test; watch both fail; Step 2/2b turn them green.)

- [ ] **Step 3: run tests** → `uv run pytest apps/backend/tests/test_azure_offsite_sync.py -q` → PASS.

- [ ] **Step 4: commit (with user approval)** — `feat(deploy): fetch/restore offsite state from the Azure restic repos (#827)`

---

### Task 6: Provisioning + Terraform — soft-delete in, WORM out, restic repos, two-SAS model

**Files:**
- Modify: `deploy/azure-provision.sh`
- Modify: `deploy/terraform/main.tf`
- Test: `apps/backend/tests/test_azure_provision_script.py`, `apps/backend/tests/test_azure_offsite_sync.py` (terraform assertions)

**Interfaces:** consumes Task 3 env names (`DOKTOK_AZURE_SAS_PRUNE`); produces: containers WITHOUT time-based immutability, account-level 30-day soft-delete, `restic init` of both prefixes, lifecycle rule scoped to legacy tarball prefixes only (never `files/`/`pg/` — a lifecycle delete inside a restic repo corrupts it).

- [ ] **Step 1: rewrite the failing tests**

`test_azure_provision_script.py` — replace `test_lifecycle_tiers_to_cool_and_expires_never_archive` and `test_immutability_and_versioning_controls` with:

```python
def test_soft_delete_replaces_worm() -> None:
    # #827: time-based WORM conflicts with restic prune (blocked deletes, 2026-08-18 incident).
    # Ransomware resistance = 30d soft-delete + no-delete sync SAS + host-only prune SAS.
    assert "--enable-delete-retention true" in SCRIPT
    assert "--delete-retention-days" in SCRIPT
    assert "immutability-policy create" not in SCRIPT
    # existing deployments get the old policies removed idempotently
    assert "immutability-policy delete" in SCRIPT


def test_lifecycle_never_touches_the_restic_repos() -> None:
    assert "management-policy create" in SCRIPT
    # expiry applies ONLY to the legacy tarball prefixes; a delete inside files//pg/ prefixes
    # would corrupt the restic repos
    assert '"prefixMatch": ["pg-repo-", "files-repo-"]' in SCRIPT
    assert "tierToArchive" not in SCRIPT  # Archive rehydration takes hours - would blow RTO


def test_restic_repos_are_initialized_and_two_sas_guidance_printed() -> None:
    assert "restic init" in SCRIPT and ":/files" in SCRIPT and ":/pg" in SCRIPT
    assert "DOKTOK_AZURE_SAS" in SCRIPT and "DOKTOK_AZURE_SAS_PRUNE" in SCRIPT
    assert "--permissions rwcl" in SCRIPT  # sync SAS: read/write/create/list, NO delete
```

`test_azure_offsite_sync.py` — replace `test_terraform_owns_the_full_stack` / `test_terraform_lifecycle_ladder` with:

```python
def test_terraform_owns_the_full_stack() -> None:
    for res in (
        "azurerm_resource_group",
        "azurerm_storage_account",
        "azurerm_storage_container",
        "azurerm_storage_management_policy",
    ):
        assert f'resource "{res}"' in TF
    assert "versioning_enabled = true" in TF
    assert "allow_nested_items_to_be_public = false" in TF
    # #827: soft-delete replaces container WORM (conflicts with restic prune)
    assert "delete_retention_policy" in TF
    assert "azurerm_storage_container_immutability_policy" not in TF


def test_terraform_lifecycle_scoped_to_legacy_tarballs() -> None:
    assert 'prefix_match = ["pg-repo-", "files-repo-"]' in TF
    assert "delete_after_days_since_modification_greater_than" in TF
```

Run both files → FAIL.

- [ ] **Step 2: azure-provision.sh changes**

In `deploy/azure-provision.sh`:
- After the versioning call, add soft-delete + immutability removal:

```bash
az storage account blob-service-properties update -n "$ACCOUNT" \
    --enable-versioning true >/dev/null
# #827: soft-delete (30d) is the safety net now; time-based WORM conflicts with restic prune.
az storage account blob-service-properties update -n "$ACCOUNT" \
    --enable-delete-retention true --delete-retention-days 30 >/dev/null
echo "removing time-based immutability policies if present (unlocked policies only)"
for c in "$CONTAINER" "${DOKTOK_AZURE_CONTAINER_LTS:-doktok-backups-lts}"; do
    az storage container immutability-policy delete --account-name "$ACCOUNT" -c "$c" \
        >/dev/null 2>&1 || warn "no (removable) immutability policy on $c - fine"
done
```

- Drop the `immutability-policy create` block (and its echo line).
- Scope the lifecycle JSON: replace `"filters": { "blobTypes": ["blockBlob"], "prefixMatch": [] }` with `"filters": { "blobTypes": ["blockBlob"], "prefixMatch": ["pg-repo-", "files-repo-"] }` and update the echo to say it only expires legacy tarballs.
- Append repo init + guidance (restic runs host-side here; provisioning requires `az login` anyway, add `require restic`):

```bash
echo "init the Azure restic repos (idempotent)"
export AZURE_ACCOUNT_NAME="$ACCOUNT" RESTIC_PASSWORD="${DOKTOK_RESTIC_PASSWORD:?set DOKTOK_RESTIC_PASSWORD}"
# init needs a writer SAS; on first provisioning use the CLI login session instead:
unset AZURE_ACCOUNT_SAS 2>/dev/null || true
for prefix in files pg; do
    restic -r "azure:${CONTAINER}:/${prefix}" init 2>/dev/null \
        && ok "repo ready: azure:${CONTAINER}:/${prefix}" \
        || ok "repo already exists: azure:${CONTAINER}:/${prefix}"
done
cat <<'EOF'
next: create TWO expiring, HTTPS-only SAS tokens on the backup account and store them off-box:
  DOKTOK_AZURE_SAS        permissions=rwcl  (NO delete)  - the hourly sync credential
  DOKTOK_AZURE_SAS_PRUNE  permissions=rwcld (delete)     - host-only, forget/prune step only
EOF
```

(If restic's Azure backend cannot use the az login session, implementer falls back to `AZURE_ACCOUNT_KEY` read via `az storage account keys list` for init only — never persisted. Verify during Task 1 spike and adjust this note accordingly.)

- [ ] **Step 3: terraform/main.tf changes**

- Delete both `azurerm_storage_container_immutability_policy` resources + the `short_worm_days`/`lts_worm_days` variables.
- In `azurerm_storage_account.backup` `blob_properties`, add:

```hcl
    delete_retention_policy {
      days = 30
    }
```

- In the management policy rule's `filters`, add `prefix_match = ["pg-repo-", "files-repo-"]` and update the rule comment: legacy tarball expiry only; restic repos manage their own retention via forget/prune.
- Update the header comment (WORM → soft-delete + SAS split) and the `sync_hint` output: `"set DOKTOK_AZURE_ACCOUNT=… DOKTOK_AZURE_CONTAINER=… (+ sync SAS rwcl-no-delete as DOKTOK_AZURE_SAS, prune SAS rwcld as DOKTOK_AZURE_SAS_PRUNE host-only)"`.

- [ ] **Step 4: run tests** → both test files PASS.

- [ ] **Step 5: commit (with user approval)** — `feat(deploy): provision soft-delete + restic repos, drop container WORM (#827)`

---

### Task 7: Hourly scheduling + env wiring

**Files:**
- Modify: `deploy/systemd/doktok-azure-sync.timer`
- Modify: `Makefile` (`dev-azure-sync` target)
- Modify: `.env.example`
- Test: `apps/backend/tests/test_azure_restic_transport.py` (extend)

**Interfaces:** produces: hourly cadence in both worlds; `make dev-azure-sync` passes `DOKTOK_AZURE_SAS_PRUNE` + `DOKTOK_RESTIC_PASSWORD` through from `.env`.

- [ ] **Step 1: failing test**

Append to `test_azure_restic_transport.py`:

```python
def test_hourly_cadence_and_env_wiring() -> None:
    timer = (REPO_ROOT / "deploy" / "systemd" / "doktok-azure-sync.timer").read_text(encoding="utf-8")
    assert "OnCalendar=hourly" in timer  # 1h offsite RPO (#827); was daily 03:47
    mk = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "DOKTOK_AZURE_SAS_PRUNE" in mk and "DOKTOK_RESTIC_PASSWORD" in mk
    ex = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "DOKTOK_AZURE_SAS_PRUNE" in ex
```

Run → FAIL.

- [ ] **Step 2: apply**

- `deploy/systemd/doktok-azure-sync.timer`: replace the comment + `OnCalendar=*-*-* 03:47:00` with:

```ini
# Hourly: the incremental transport uploads only churn, so the 1h offsite RPO is cheap (#827).
OnCalendar=hourly
```

(and update the `[Unit] Description` "daily" → "hourly, incremental".)

- `Makefile` `dev-azure-sync`: add two more grep-passthroughs in the export line:

```make
		DOKTOK_AZURE_SAS_PRUNE="$$(grep '^DOKTOK_AZURE_SAS_PRUNE=' .env | cut -d= -f2-)" \
		DOKTOK_RESTIC_PASSWORD="$$(grep '^DOKTOK_RESTIC_PASSWORD=' .env | cut -d= -f2-)" \
```

(same two greps for `dev-azure-fetch`, but WITHOUT the prune SAS — fetch only reads Azure; the delete-capable credential must not widen to paths that never prune). Drop the now-dead `DOKTOK_GFS_BASE_CLASS` passthrough.

- `.env.example`: in the Azure section replace the SAS comment block with:

```
# Sync SAS (hourly writer): permissions rwcl, NO delete, HTTPS-only, expiring. Ransomware layer.
#DOKTOK_AZURE_SAS=
# Prune SAS: permissions rwcld (delete-capable), host-only; used solely by the forget/prune step.
#DOKTOK_AZURE_SAS_PRUNE=
```

- [ ] **Step 3: run test** → PASS.

- [ ] **Step 4: commit (with user approval)** — `feat(deploy): hourly offsite cadence + two-SAS env wiring (#827)`

---

### Task 8: drp-selftest offsite round-trip leg

**Files:**
- Modify: `deploy/drp-selftest.sh`
- Modify: `Makefile` (drp-selftest target: Azure env passthroughs, else the leg silently skips)
- Test: `apps/backend/tests/test_azure_restic_transport.py` (extend)

**Interfaces:** consumes the Task 3 helpers + a throwaway Azure prefix `azure:<container>:/selftest-<ts>`; gated on Azure env present (skip otherwise, matching the eval harnesses' live-config philosophy). The payload lives under `$BACKUP_DIR` so the backup-runner container sees it (compose mode mounts `$BACKUP_DIR` at `/backups`); the compare accounts for restic recreating the snapshot's absolute path under the restore target.

- [ ] **Step 1: failing test**

```python
def test_drp_selftest_has_an_offsite_roundtrip_leg() -> None:
    st = (REPO_ROOT / "deploy" / "drp-selftest.sh").read_text(encoding="utf-8")
    assert "selftest-" in st            # throwaway Azure prefix
    assert "offsite_restic" in st       # uses the same transport helpers as the real sync
    assert "restore latest" in st       # restores the seed tree back
    # the make target passes the Azure env through, else the leg silently skips
    mk = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    m = re.search(r"drp-selftest:.*?deploy/drp-selftest\.sh", mk, re.S)
    assert m and "DOKTOK_AZURE_SAS_PRUNE" in m.group(0)
```

(add `import re` to the test file's imports if absent.)

Run → FAIL.

- [ ] **Step 2: append the leg to `deploy/drp-selftest.sh`** (after the existing checks; follow the script's existing ok/warn style):

```bash
# Offsite round-trip (#827): seed a tiny tree -> backup to a throwaway Azure prefix -> restore ->
# compare content. Live-config (needs DOKTOK_AZURE_* + DOKTOK_RESTIC_PASSWORD); skipped without
# it, like the eval harnesses. Never touches the real repos.
if [ -n "${DOKTOK_AZURE_ACCOUNT:-}" ] && [ -n "${DOKTOK_AZURE_SAS:-}" ]; then
    # offsite_restic is mode-aware: default to compose on the dev box (restic lives in the
    # backup-runner image there); prod sets DOKTOK_DEPLOY_MODE=host via backup.env.
    mode="${DOKTOK_DEPLOY_MODE:-compose}"
    COMPOSE_FILES="${DOKTOK_COMPOSE_FILES:-docker-compose.yml,docker-compose.dev.yml}"
    COMPOSE_ENV_FILE="${DOKTOK_COMPOSE_ENV_FILE:-.env}"
    compose=(docker compose)
    for f in ${COMPOSE_FILES//,/ }; do compose+=(-f "$f"); done
    compose+=(--env-file "$COMPOSE_ENV_FILE")
    st_id="selftest-$(date -u +%Y%m%d%H%M%S)"
    # The payload lives under BACKUP_DIR so the backup-runner sees it too (compose mode mounts
    # $BACKUP_DIR at /backups); restic restores recreate the snapshot's absolute path under the
    # target, so the compare appends the runner-visible payload path.
    st_payload="$BACKUP_DIR/.$st_id"
    st_out="$BACKUP_DIR/.$st_id-out"
    mkdir -p "$st_payload" "$st_out"
    echo "drp selftest payload $(date -u)" >"$st_payload/payload.txt"
    if [ "$mode" = "compose" ]; then
        st_payload_rt="/backups/.$st_id"; st_out_rt="/backups/.$st_id-out"
    else
        st_payload_rt="$st_payload"; st_out_rt="$st_out"
    fi
    offsite_azure_env sync
    export RESTIC_PASSWORD="$DOKTOK_RESTIC_PASSWORD"
    RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/$st_id" offsite_restic init
    RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/$st_id" \
        offsite_restic backup "$st_payload_rt" --no-lock >/dev/null
    RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/$st_id" \
        offsite_restic restore latest --no-lock --target "$st_out_rt" >/dev/null
    diff -r "$st_payload" "$st_out$st_payload_rt" >/dev/null && ok "offsite round-trip: content identical" \
        || { err "offsite round-trip MISMATCH"; exit 1; }
    # teardown needs delete rights: prune SAS when present, else leave the prefix (lifecycle cleans)
    if [ -n "${DOKTOK_AZURE_SAS_PRUNE:-}" ]; then
        offsite_azure_env prune
        RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/$st_id" \
            offsite_restic forget --prune --keep-last 0 >/dev/null 2>&1 || true
    fi
    rm -rf "$st_payload" "$st_out"
else
    warn "offsite round-trip skipped (no DOKTOK_AZURE_* env)"
fi
```

NOTE: plain `restic init` — restic 0.14's init has no `--no-lock` flag (spike 2026-08-18).

- [ ] **Step 2b: wire the env passthrough in the Makefile** (else `make drp-selftest` silently skips the leg — recipes do not inherit `.env`):

In the `drp-selftest` target, replace `@deploy/drp-selftest.sh` with:

```make
	@export DOKTOK_AZURE_ACCOUNT="$$(grep '^DOKTOK_AZURE_ACCOUNT=' .env | cut -d= -f2-)" \
		DOKTOK_AZURE_CONTAINER="$$(grep '^DOKTOK_AZURE_CONTAINER=' .env | cut -d= -f2-)" \
		DOKTOK_AZURE_SAS="$$(grep '^DOKTOK_AZURE_SAS=' .env | cut -d= -f2-)" \
		DOKTOK_AZURE_SAS_PRUNE="$$(grep '^DOKTOK_AZURE_SAS_PRUNE=' .env | cut -d= -f2-)" \
		DOKTOK_RESTIC_PASSWORD="$$(grep '^DOKTOK_RESTIC_PASSWORD=' .env | cut -d= -f2-)"; \
		deploy/drp-selftest.sh
```

(SAS_PRUNE is included here on purpose: the leg's teardown prunes its own throwaway prefix — the only delete-capable passthrough outside dev-azure-sync.)

- [ ] **Step 3: run test** → PASS.

- [ ] **Step 4: commit (with user approval)** — `test(deploy): drp-selftest offsite round-trip leg (#827)`

---

### Task 9: Docs, changelog, ADR

**Files:**
- Modify: `docs/operations/backup-and-recovery.md` (offsite sections: transport, two-SAS model, restore-from-Azure via restic, legacy tarball expiry note incl. the ~2026-08-27 WORM unlock for the Jul-28 LTS sets; also the stale immutability/WORM description at ~line 152 flagged by Task 6 review)
- Modify: `docs/operations/running.md` (dev crontab line: `47 3 * * *` → `7 * * * *` for dev-azure-sync; mention hourly)
- Modify: `docs/operations/security-runbook.md` (~line 195: container immutability/WORM → soft-delete + two-SAS model)
- Modify: `deploy/systemd/README.md` (timer cadence; stale immutability-policy import doc at ~lines 26-27)
- Modify: `deploy/terraform/README.md` (stale `azurerm_storage_container_immutability_policy` import mention)
- Modify: `deploy/terraform/main.tf` (`delete_after_days` variable description still says the floor "is enforced by deploy/azure-sync.sh (audit)" — reference the new transport)
- Modify: `.env.example` (drop the stale GFS "class each upload lands in" comment + `#DOKTOK_GFS_BASE_CLASS=daily` line; the GFS retention now lives in azure-sync.sh's forget flags)
- Create: `docs/adr/ADR-0026-incremental-offsite-restic-transport.md`
- Modify: `CHANGELOG.md` ([Unreleased] → Changed entry referencing #827)

**Interfaces:** ADR follows the existing ADR format (context/decision/consequences, references #827, amends #766's tarball design, links the spec `docs/superpowers/specs/2026-08-18-incremental-offsite-backup-design.md`).

- [ ] **Step 1: ADR-0026** — content: context = scale table from the spec + 2026-08-18 incident; decision = restic end-to-end transport, hourly, GFS via forget/prune, soft-delete + two-SAS replacing container WORM, tarball fallback one release; consequences = storage ~linear with data, offsite RPO 1h both legs, prune credential handling, shelved option B (pgBackRest `repo1-type=azure`) documented with its trigger (~1-min offsite pg RPO need).
- [ ] **Step 2: runbook + docs edits** (as listed; keep the existing docs' voice; no new TODO markers anywhere — deferred work goes in docstrings/ADR, per repo convention).
- [ ] **Step 3: CHANGELOG entry** under `[Unreleased] / Changed`: the transport swap, the cadence change (daily 03:47 → hourly), the WORM→soft-delete+two-SAS change, and the one-release `DOKTOK_OFFSITE_TRANSPORT=tarball` escape hatch.
- [ ] **Step 4: commit (with user approval)** — `docs: ADR-0026 + runbooks for the incremental offsite transport (#827)`

---

### Task 10: Live dev-box migration + end-to-end verification

**Files:** none in repo (live environment; the user runs/approves each externally-visible step).

- [ ] **Step 1: rebuild the runner image** (it bakes deploy/): `docker compose -f docker-compose.yml -f docker-compose.dev.yml build backup-runner`
- [ ] **Step 2: provision the account changes**: `az login` session → run the updated `deploy/azure-provision.sh` (soft-delete on, immutability policies deleted, lifecycle rescoped, restic repos initialized).
- [ ] **Step 3: create the two SAS tokens** per the provision output; put `DOKTOK_AZURE_SAS_PRUNE` in `.env` (rotate the existing `DOKTOK_AZURE_SAS` to a no-delete one).
- [ ] **Step 4: first sync**: `make dev-azure-sync` → expect `offsite sync complete (offsite snapshots pg=1 files=N …)`; `cat backups/status/offsite.json` ⇒ `"ok":true`; DRP panel offsite OK.
- [ ] **Step 5: switch the dev crontab** `47 3 * * *` → `7 * * * *` for the dev-azure-sync line (back up first: `crontab -l > /tmp/doktok-crontab.bak`).
- [ ] **Step 6: round-trip proof**: `make drp-selftest` (offsite leg not skipped) + a manual `make dev-azure-fetch TS=` restore drill per the runbook.
- [ ] **Step 7: legacy cleanup note**: on/after ~2026-08-27 delete the WORM-locked Jul-28 LTS duplicates (4.65 GB) — or let the rescoped lifecycle rule expire the legacy `*-repo-*` tarballs automatically (verify the delete_after_days value covers them).

## Self-review notes (already applied)

- Spec coverage: transport rewrite (T4), fetch (T5), provisioning/TF (T6), cadence (T7), selftest (T8), docs/ADR (T9), migration (T10), rollback flag (T2), sentinel contract (T4), scale rationale (spec/ADR). All spec sections map to a task.
- Type/name consistency: `offsite_restic`, `offsite_azure_env`, `offsite_snapshot_count` used identically across Tasks 3/4/5/8; `DOKTOK_AZURE_SAS_PRUNE` spelled consistently; tarball fallback path name `deploy/azure-sync-tarball.sh` consistent.
- Known leftover risk: restic `--no-lock` on `init` varies by version — Task 1 spike settles it, and Task 6's init note carries the fallback.
