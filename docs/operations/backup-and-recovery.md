# Backup & recovery (local-first, Azure offsite)

The running, low-RPO backup design for the hybrid deployment (M12). **Local-first:** every backup is
staged into a local repository folder first, then a thin step ships it offsite to Azure Blob - so the
whole engine works and is testable with no cloud account. See also
[backup-restore-consistency.md](backup-restore-consistency.md) (the app-level guarantees) and the
M12 epic (#366).

## Targets

| Data | RPO | Mechanism |
|---|---|---|
| Postgres | ~1 min | pgBackRest: continuous WAL archiving + base/diff backups (PITR) |
| files_root | ~15 min | restic dedup snapshots (encrypted) |
| Offsite (both) | ~1 h | `azure-sync.sh` to Azure Blob (immutability + versioning account-side) |

RTO ~2-4 h to a working stack (restore is bandwidth-bound from Azure; keep recent backups in a
Hot/Cool tier, not Archive, so rehydration latency doesn't blow RTO).

## Layout

```
$DOKTOK_BACKUP_DIR/            # default ./backups (gitignored); on the box a real volume
  files/                      # restic repo (files_root: dedup + AES-256)
  pg/                         # pgBackRest repo (base + WAL, aes-256-cbc)
  pgbackrest.conf             # generated from env (chmod 600)
  status/                     # per-leg freshness sentinels (JSON, chmod 0644) - see below
    history.jsonl             # append-only, tamper-evident backup-event history (chmod 0644)
    requests/drill.request    # on-demand drill request file (dropped by the backend, consumed by a root path-unit)
```

## Freshness sentinels (the DRP source of truth)

Each backup leg writes one JSON file under `$DOKTOK_BACKUP_DIR/status/<leg>.json` (written by
`write_status` in `deploy/lib.sh`, atomically via a temp file + rename, `chmod 0644` so the backend -
a different uid in compose - can read it). The sentinels live **outside** Postgres so a DB restore
can't roll backup status back. They feed `deploy/check-backup-freshness.sh`, the `/metrics` gauges,
and the Settings -> DRP panel.

Schema (common fields plus optional per-leg metrics captured during the backup, M12 #380):

```json
{
  "leg": "files",
  "ok": true,
  "last_run_at": "2026-06-25T20:00:00Z",
  "detail": "restic snapshot",
  "size": "662.7 MiB",
  "file_count": 287,
  "backup_id": "a1b2c3d4"
}
```

- `last_run_at` is normally the run time, but for the **pg** leg it is the **recovery point** - the
  last archived WAL time, stamped each minute by `deploy/pg-wal-freshness.sh` via the
  `WRITE_STATUS_TS` override (see below).
- **files** leg (restic, parsed from `restic backup` output in `deploy/backup-files.sh`): `size`,
  `file_count`, `backup_id` (snapshot id).
- **pg** leg (pgBackRest, parsed from `pgbackrest info --output=json` by the `pg_backup_extra` helper
  in `deploy/lib.sh`): `size` (database size), `backup_id` (backup label), plus `wal_lag_s` (seconds
  since the last archived WAL) added by `pg-wal-freshness.sh`.

## Backup-event history (append-only, tamper-evident)

The sentinels answer "**is the latest backup of each leg fresh?**" (a latest-state snapshot, overwritten
each run). They cannot answer "**what happened over time?**" - a failed run that is later retried
overwrites the failure. That timeline lives in a separate, append-only history file:

```
$DOKTOK_BACKUP_DIR/status/history.jsonl       # one JSON object per line, newest at the bottom
$DOKTOK_BACKUP_DIR/status/history.jsonl.1     # previous segment after rotation (~5000 lines)
```

`log_event` in `deploy/lib.sh` appends one line per event (the compose-mode wrapper is
`deploy/log-event.sh`, used when a step runs inside the `backup-runner` container). Like the
sentinels, the history lives **outside Postgres** (same dir, `chmod 0644`) so a DB restore cannot roll
the timeline back, and it is shipped offsite with the rest of the repo.

**Who emits events:** `backup-files.sh` and `backup-pg.sh` (per leg: `start`, then `success` with
metrics, or `failure`; plus a `prune` event after retention), `backup.sh` (the compose pre-deploy path
records a `pg success`), and `restore-drill.sh` (`drill_pass` / `drill_fail`).

Line schema (fields are **whitelisted** - only `detail` is free text, and it is JSON-escaped and
truncated to ~200 chars host-side; no secrets, command lines, raw stderr, filenames, or tenant /
document content ever enter it):

```json
{"schema":1,"seq":42,"prev_sha256":"<hex>","ts":"2026-06-25T20:00:00Z","leg":"files","event":"success","ok":true,"size":"662 MiB","item_count":287,"backup_id":"a1b2c3d4","duration_ms":48213,"detail":"restic snapshot"}
```

- `leg`: `files` | `pg` | `offsite` | `drill` | `prune`
- `event`: `start` | `success` | `failure` | `prune` | `drill_pass` | `drill_fail`
- `seq` + `prev_sha256` form a **tamper-evident hash chain**: each line carries a monotonically
  increasing sequence number and the SHA-256 of the **previous** line. A reader that recomputes the
  chain can detect any edited, reordered, deleted, or truncated line - that is what the API's
  `integrity_ok=false` (below) signals. `seq` continues across rotation so the counter never resets.
- **Rotation:** when the file passes ~5000 lines (`DOKTOK_HISTORY_MAX_LINES`) it is rolled to
  `history.jsonl.1` and a fresh file is started whose first line chains off the last archived line, so
  the chain is unbroken across the rotation boundary.
- **Concurrency:** appends are serialized with `flock` (when available) so two legs running at once
  cannot interleave and corrupt the chain; otherwise a single `O_APPEND` write is the best-effort
  fallback.

The sentinels and the history are complementary: **sentinels = "is the latest backup fresh"**
(source of truth for the DRP state badges and `/metrics`); **history = "what happened over time"**
(source of truth for the timeline, see the API and the activity-log mirror below).

## Scripts (`deploy/`)

| Script | Purpose |
|---|---|
| `backup-files.sh` | restic snapshot of files_root -> local repo (+ prune/retention) |
| `restore-files.sh <target> [snap]` | restore files_root from the local repo |
| `backup-pg.sh [full\|diff\|incr]` | pgBackRest backup -> local repo |
| `restore-pg.sh ["<time>"]` | pgBackRest restore / PITR into `$DOKTOK_PGDATA` |
| `azure-sync.sh [--dry-run]` | push the local repo to Azure Blob (offsite leg) |
| `test-pitr.sh` | self-contained proof that folder-based WAL+base+PITR works (throwaway containers) |
| `restore-drill.sh` | live restore drill into throwaway dirs/containers; asserts row counts + records RPO/RTO (drill sentinel + history) |
| `log-event.sh` | compose-mode wrapper to append one history event (`log_event`) from inside `backup-runner` |
| `backup.sh` / `restore.sh` | the dependency-light pre-deploy snapshot (pg_dump + tar), called by deploy.yml |

`backup.sh` (M11/DEVOPS-6) is the simple, Docker-only **pre-deploy snapshot**; the restic/pgBackRest
scripts above are the scheduled **low-RPO running backups**.

## What is verified locally vs box-side

- **Verified here:** files backup -> restore round-trips identically and the restic repo is
  encrypted; the folder-based WAL+base+PITR mechanism (`test-pitr.sh`: restore to a target time
  contains only pre-target data). The `repair`/`verify-restore` reconciler runs against the live DB.
- **Box-side (needs the host / Azure account):** wiring Postgres `archive_command=pgbackrest
  --stanza=doktok archive-push %p` into the db container (DB-A1); systemd timers + resource caps;
  Azure account, container immutability/versioning/lifecycle; the firewall. These are M12 infra
  tickets to apply on the N95.

## Back up (scheduled on the box)

```bash
# files (every ~15 min) and Postgres (base/diff per schedule); then offsite
DOKTOK_RESTIC_PASSWORD=... ./deploy/backup-files.sh
DOKTOK_PGDATA=/var/lib/postgresql/data DOKTOK_PGBACKREST_CIPHER_PASS=... ./deploy/backup-pg.sh diff
DOKTOK_AZURE_ACCOUNT=... DOKTOK_AZURE_CONTAINER=... DOKTOK_AZURE_SAS=... ./deploy/azure-sync.sh
```

Wrap with the quiesce hook for a still snapshot: `doktok-worker quiesce` -> back up -> `quiesce --off`.

### On demand: `make backup` (dev / any host, no systemd needed)

`make backup` runs the same mode-aware `deploy/backup.sh` the timers run, so you can populate the DRP
(sentinels + history) on demand without systemd - useful in **dev**, where there is no host scheduler.
It honours `DOKTOK_DEPLOY_MODE`; override the type with `TYPE=full|diff|incr` (default `full`).

- **Treat dev as a device (recommended):** run the prod compose stack locally and set
  `DOKTOK_DEPLOY_MODE=compose` (in `.env`) - then `make backup` uses the same containerized engine as
  the box (the `backup-runner` + pgBackRest + WAL-archiving db image), and the DRP fills in exactly as
  on the device. The lightweight dev `docker-compose.yml` (db + gotenberg only) has no backup engine.
- **Bare host mode** (`DOKTOK_DEPLOY_MODE=host`) needs the host tools: `restic` (`brew install restic`)
  for the files leg, and pgBackRest for the pg leg (awkward on macOS - prefer the compose path above).
- The **portable backup** (Settings -> DRP -> Create/Download) and `make drp-selftest` work in any dev
  with just `pg_dump` (`brew install libpq && brew link --force libpq` gives you pg_dump@17).

Note: scheduled backups still require the host **systemd timers** (Linux only); `make backup` is the
on-demand equivalent for dev/macOS.

## Restore

1. (If offsite) pull the repo from Azure into `$DOKTOK_BACKUP_DIR` (reverse of `azure-sync.sh`).
2. Stop the stack. Restore Postgres to a point at or before the latest files snapshot:
   `DOKTOK_PGDATA=... DOKTOK_PGBACKREST_CIPHER_PASS=... ./deploy/restore-pg.sh "2026-06-16 20:00:00+00"`
3. Restore files_root (>= the DB restore point): `./deploy/restore-files.sh /var/lib/doktok/files`
4. Start the stack, then `doktok-worker repair` to reconcile DB <-> files and re-queue any
   re-derivable gaps (the reconciler backfills derived artifacts).

## Recover onto a new / different device

The backup repos are **portable** - a backup taken on one device restores on another (this is the
real test of a backup). restic (files) is content-addressed + encrypted; pgBackRest (Postgres)
restores its repo into a fresh data dir. Three things must travel with the repos:

1. **The repos** - the whole `$DOKTOK_BACKUP_DIR` (its `files/`, `pg/`, and `status/` dirs). With
   offsite (Azure) deferred, backups currently live only on the source device's local disk, so
   moving A -> B today means **copying `$DOKTOK_BACKUP_DIR` by hand** (USB / `scp` / rsync). (Once
   offsite ships, B pulls the repo instead - step 1 of [Restore](#restore).)
2. **The passphrases** - `DOKTOK_RESTIC_PASSWORD` and `DOKTOK_PGBACKREST_CIPHER_PASS`. They are NOT
   in the backup (off-box by design); the repos are unrecoverable without them. Carry
   `DOKTOK_SECRETS_KEY` too - `app_settings` (incl. the OpenAI key) is in the DB backup and B needs
   the same key to decrypt the stored OpenAI key (everything else restores regardless; without it you
   just re-enter the key in Settings).
3. **The same PostgreSQL major version** - pg17 -> pg17 (`restore-pg.sh` refuses a mismatch; #356).
   Dev and the device both run `pgvector/pgvector:pg17`, so this holds across them.

This procedure is the same whether the source is a device or the **dev** machine - DRP treats dev as
a device (host mode). It is OS-independent: a restic/pgBackRest repo made on dev (macOS) restores on
the Linux box. (Caveat: dev's compose `db` isn't wired for pgBackRest WAL archiving, so a real `pg`
repo only exists in dev if you ran a host-mode pg backup there; the **files** repo is always
portable. `test-pitr.sh` proves PITR in dev but produces no persistent repo.)

### Steps on the target device

```bash
# 0) Get the code + secrets onto B
#    - deploy the app (e.g. `make deploy-box`) but DO NOT start the stack yet
#    - copy the backup repo from A:
scp -r A:/var/lib/doktok/backups  /var/lib/doktok/backups          # or USB/rsync
#    - put the three secrets in /opt/doktok/.env.production (DB_PASSWORD, RESTIC_PASSWORD,
#      PGBACKREST_CIPHER_PASS, SECRETS_KEY, TENANT_TOKENS, ...) - see the fresh-box runbook section 3.

# 1) Restore Postgres into a fresh data dir (server STOPPED), then files_root, in one shot:
export DOKTOK_BACKUP_DIR=/var/lib/doktok/backups
export DOKTOK_PGDATA=/path/to/empty/pgdata           # the target cluster's data dir
export DOKTOK_PGBACKREST_CIPHER_PASS=...  DOKTOK_RESTIC_PASSWORD=...
./deploy/restore.sh /var/lib/doktok/files            # pg (latest) + files; add a PITR time as arg 2

# 2) Start the stack, then reconcile DB <-> files and backfill any re-derivable gaps:
doktok-worker repair
```

`restore.sh` restores Postgres **at or before** the files snapshot (the safe-restore rule: files
restore point >= DB restore point), then `restore-files.sh`. Restores are **destructive** - run them
on a fresh/empty target (or stage + swap), never against a live `files_root`/`PGDATA`. In compose
mode, run the equivalent inside the db container / backup-runner as the existing scripts do; the
contract and `$DOKTOK_BACKUP_DIR` layout are identical in both modes.

> Simpler one-file path coming: a single downloadable `.tgz` (DB + files) with UI download/restore is
> in design - it will become the easiest A -> B path for non-PITR full recovery. This section covers
> the current restic/pgBackRest mechanism.

## Box-side: scheduling, monitoring, offsite

- **WAL archiving:** the prod `db` image (`deploy/docker/db.Dockerfile`) bundles pgBackRest; the
  compose `db` service sets `archive_command=pgbackrest --stanza=doktok archive-push %p` so WAL ships
  continuously into the local repo (`deploy/pgbackrest/pgbackrest.conf`, cipher pass from env). It
  also sets `archive_timeout=60` so an idle DB still switches and ships one WAL segment each minute,
  keeping the recovery point fresh.
- **Scheduling:** host systemd timers run the scripts capped (`Nice`/`idle` IO/`MemoryMax`) so they
  never starve OCR/ingest. The shipped units are installed by `deploy/install-systemd.sh` (run as
  root, after writing `/etc/doktok/backup.env`): `doktok-backup-diff` (hourly -> `backup.sh diff`),
  `doktok-backup-full` (weekly Sun 03:00 -> `backup.sh full`), and `doktok-pg-wal-freshness` (every
  minute -> `pg-wal-freshness.sh`). The units are **mode-aware** via `DOKTOK_DEPLOY_MODE` in
  `backup.env`: in `compose` mode they drive the containerized tools; in `host` mode they run the host
  tools. The azure-sync / check-backup / restore-drill / ollama-autostop timers are documented as
  example units in `deploy/systemd/README.md`.
- **pg WAL-freshness:** `deploy/pg-wal-freshness.sh` (every minute) stamps the pg sentinel's
  `last_run_at` to the last archived WAL time (the real recovery point) and records `wal_lag_s`,
  **preserving** the base backup's `size`/`backup_id`. Without it the pg leg would flap "stale"
  between the hourly/weekly base backups even though continuous WAL archiving holds the ~60s RPO.
- **Freshness/monitoring:** each script writes a per-leg sentinel `$DOKTOK_BACKUP_DIR/status/<leg>.json`
  (outside Postgres, so a DB restore can't roll status back) carrying the captured size/file-count/id
  metrics (see [Freshness sentinels](#freshness-sentinels-the-drp-source-of-truth) above).
  `deploy/check-backup-freshness.sh` alerts on stale/failed legs; the same sentinels feed the
  `/metrics` gauges and the Settings -> DRP panel; `.github/workflows/backup-watchdog.yml` is an
  independent off-box watchdog.
- **Restore drills:** `deploy/restore-drill.sh` restores the latest files snapshot + runs the
  Postgres PITR proof into throwaway locations, asserts row counts, and records measured RPO/RTO.
  Scheduled weekly and triggerable on demand - see [Recovery drills](#recovery-drills) below.
- **Azure offsite:** provision once with `deploy/azure-provision.sh` (account + container + versioning
  + time-based immutability); `azure-sync.sh` pushes the local repo. Keep recent backups Hot/Cool.

## Settings -> DRP panel

The backend reads the sentinels and exposes them at `GET /drp` (`apps/backend/doktok_api/routers/settings.py`,
`DrpStatus`/`BackupLegStatus` in `contracts/doktok_contracts/schemas.py`). For each leg the backend
**derives a state** from the sentinel: `ok` is `false` -> **failed**; else age `> 3 x` the leg's
target RPO -> **stale**; else **ok**; a missing or never-run sentinel -> **unknown**.

The Settings -> **DRP** tab (`apps/ui/src/SettingsPanel.tsx` `DrpSection`, polled every 45s) renders
one status **card** per leg - **Files (restic)**, **Postgres (pgBackRest)**, **Offsite (Azure)**, and
**Last restore drill**. Each card shows:

- a colour-coded state **badge** - green `ok`, amber `stale`, red `failed`, grey `unknown` (the badge
  text is the state word itself; colour comes from CSS in `apps/ui/src/styles.css`);
- the last-run **age** (for the pg leg this is the WAL recovery point, not the base-backup time);
- the captured **metrics** as a small list: `Size`, `Files` (files leg), `ID` (restic snapshot id /
  pgBackRest label, truncated with the full id in a tooltip), and the leg's `Target RPO`.

A **WAL shipping lag** note under the grid surfaces `wal_lag_seconds` (from the pg sentinel's
`wal_lag_s`), and a configuration block shows the repo location, deploy mode, and Azure container.

### Backup-history window + API

`GET /api/v1/settings/drp/history?limit=&leg=` (`apps/backend/doktok_api/routers/settings.py`,
returning `DrpHistoryResponse` in `contracts/doktok_contracts/schemas.py`) is a **read-only window**
over `history.jsonl`. The reader is a bounded tail read in the Postgres repo adapter (behind the
`AppSettingsRepository` port): newest-first, capped by `limit` (default 100, max 500), optionally
filtered by `leg`, skipping malformed lines and **never returning 500** even if the file is missing or
unreadable.

Response fields:

- `events`: the projected `BackupEvent[]` (the wire model exposes `ts`/`leg`/`event`/`ok`/`size`/
  `item_count`/`backup_id`/`duration_ms`/`detail`/`seq`; the chain fields `prev_sha256`/`schema` are
  **deliberately not exposed** - only `seq` is, so a consumer can see ordering).
- `source_available`: false when the history file does not exist yet (a never-backed-up box).
- `total_returned`: number of events in this window.
- `truncated`: true when more events exist than the read cap surfaced.
- `integrity_ok`: false when the `prev_sha256` hash chain is broken across the read window - i.e. the
  authoritative history was edited, reordered, or truncated. **This is the tamper signal.**

The Settings -> **DRP** tab renders this as a **backup-history table** below the status cards
(`apps/ui/src/SettingsPanel.tsx`, `BackupHistory`): a **leg filter**, a neutral "No backup history
yet" empty state, a **"truncated" footer** when the window is capped, and - when `integrity_ok` is
false - a prominent **red integrity-failure banner** ("Backup history integrity check failed - the log
may be tampered or corrupt"). Treat that banner as a real incident, not a display glitch: the
authoritative log no longer verifies.

## Recovery drills

An untested backup is not a backup. `deploy/restore-drill.sh` proves the backups actually restore,
into **throwaway** containers/dirs only (it touches no production data), and records the outcome in
**both** the `drill` sentinel (latest-state, drives the DRP "Last restore drill" card) and the
append-only history (`drill_pass` / `drill_fail`).

What a drill now proves:

1. **Files restore is non-empty:** restores the latest restic snapshot into a temp dir and asserts the
   restored file count is `> 0`.
2. **Postgres PITR + row count:** runs the self-contained PITR proof (`test-pitr.sh`), which restores
   a base backup + WAL to a target time in a throwaway container and asserts the restored core table
   is non-empty (`> 0` rows) - i.e. the recovered database is queryable and carries data.
3. **Measured RPO/RTO:** records **RPO** (now minus the latest archived WAL recovery point, taken from
   the pg sentinel's `last_run_at`) and **RTO** (wall-clock of the whole drill, a proxy for
   time-to-recover), plus an `evidence` string (e.g. `files=287 rows(document)=1 rpo=42s rto=118s`)
   stamped into the sentinel `detail` and the history line.

### Weekly timer

`doktok-restore-drill.{service,timer}` runs the drill **weekly** (Sun 03:00, randomized delay,
resource-capped) and is installed by `deploy/install-systemd.sh`. See
[deploy/systemd/README.md](../../deploy/systemd/README.md) for the unit shapes and cadence.

### Run a drill on demand

The Settings -> DRP tab has a **"Run drill now"** button that calls
`POST /api/v1/settings/drp/drill` and then polls the DRP status for the result. The flow is designed
so the **backend never execs anything as root**:

1. The backend only **drops a fixed, argument-free request file** at
   `$DOKTOK_BACKUP_DIR/status/requests/drill.request`. It rejects with **429** if a request is already
   pending, or if the last drill ran within the **10-minute cooldown** (`_DRILL_COOLDOWN_SECONDS`,
   measured from the drill sentinel's `last_run_at`).
2. A **root** systemd path-unit (`doktok-restore-drill-ondemand.path`) watches that file; when it
   appears, the matching service deletes the request file first (so a failed drill can't loop) and
   runs the one drill under `flock` (single-flight, so it can never overlap the weekly timer) with its
   own systemd `StartLimit` cooldown backing up the backend's rate-limit.

The same request file is also dropped by no other path, so the backend's two 429 rate-limits plus the
root-side `flock`/`StartLimit` give at most one on-demand drill per ~10 minutes.

## Activity-log mirror (non-authoritative)

For convenience, terminal backup/drill events are also **mirrored** into the in-DB activity log (the
Settings -> Activity tab). The mirror is explicitly **non-authoritative**, **idempotent**, and
**forward-only**: only the events surfaced by a `/drp/history` read are mirrored (the whole file is
never replayed), each row gets a deterministic id derived from `(seq, ts, leg, event)` so re-reads
collapse to one row, and every row is tagged `{"source":"drp","authoritative":false}`.

The mapping (`_MIRROR_EVENT_TYPES`) uses three new `AuditEventType` values - `backup.completed`
(a leg `success`), `backup.failed` (a leg `failure` or `drill_fail`), and `drill.completed`
(`drill_pass`); transient `start`/`prune` events are skipped as noise.

**The history file remains the source of truth.** Because the mirror lives inside Postgres, a DB
restore rolls the mirror back, but **not** the `history.jsonl` timeline. If the Activity tab and the
DRP history table ever disagree, trust the DRP history.

## Testing the DRP

An untested backup is not a backup. The restore *apply* runs via a root systemd path-unit, so the
full destructive path needs a Linux host; everything else is testable anywhere with Docker.

- **Tier 0 - automated (CI):** `deploy/test-pitr.sh` (PITR proof) + the backup/restore unit +
  integration tests run on every push.
- **Tier 1 - one command, no risk (dev or any box):** `make drp-selftest` runs, in throwaway
  containers that touch no real data, the PITR proof **and** a portable export -> encrypt -> decrypt
  -> restore round-trip (`deploy/restore-roundtrip.sh`) asserting rows + a pgvector value + a file
  come across A -> B. Also: create + download a backup in Settings -> DRP, then **Check backup** on
  the downloaded `.tgz.enc` (a non-destructive preview validates the real archive end-to-end).
- **Tier 2 - cross-device (the real DR test, no risk to prod):** stand up a throwaway second stack,
  export from A, restore into B, and confirm documents/search/chat came across. `restore-roundtrip.sh`
  proves the data/format leg of this self-contained; the full app-level B is a manual stand-up.
- **Tier 3 - in place on the box:** real backups, "Run drill now", and a real restore. The restore
  takes a **mandatory pre-restore safety snapshot and rolls back on failure**, so it is recoverable
  even in place - but Tier 2 (a separate B) is the safer first proof.

### Step-by-step on a dev machine

Dev (macOS/host) has no systemd, so the **destructive restore *apply* cannot run on macOS** (it needs
the root path-unit on Linux). Everything else - making a backup, downloading it, and **validating that
it would restore** - is fully testable in dev. Three things you can do, fastest first:

**1. One-command proof (no setup beyond Docker):**
```bash
make drp-selftest
```
Runs the PITR proof + a full portable export -> encrypt -> decrypt -> restore round-trip in throwaway
containers, asserting rows + a pgvector value + a file survive A -> B. Touches nothing real.

**2. Exercise the real backup + recovery-validate UI (your actual data):**
```bash
brew install libpq && brew link --force libpq   # gives dev pg_dump@17 (one-time)
# start the dev stack + backend/worker as usual, open Settings -> DRP
```
- **Make a backup:** the "Portable backup" card -> set a passphrase -> **Create backup** -> **Download**
  the `.tgz.enc`. (Or `make backup` to populate the DRP sentinels + history - see "On demand" above.)
- **Test recovery non-destructively:** "Restore from a backup file" -> pick the `.tgz.enc` you just
  downloaded + the passphrase -> **Check backup**. The *preview* decrypts, safe-extracts, verifies the
  manifest checksums/HMAC, and checks version compatibility - i.e. it proves the archive **would**
  restore - **without** wiping anything. This is the safe dev recovery test. Do NOT run the final
  "Restore now" against your dev data unless you mean to replace it (and that step only executes on a
  Linux host anyway).

**3. Full destructive round-trip (recover onto a clean instance):** to actually exercise the wipe +
import, do it on Linux - either `deploy/restore-roundtrip.sh` (self-contained, throwaway containers,
proves the data/format leg), or run the **prod compose stack** locally with `DOKTOK_DEPLOY_MODE=compose`
+ `sudo ./deploy/install-systemd.sh` so the restore path-unit exists, then drive the full
upload -> preview -> confirm -> apply flow. See [Recover onto a new / different
device](#recover-onto-a-new--different-device).

## Gotchas

- **Run pgBackRest in the db container as the `postgres` user.** `deploy/backup.sh` runs
  `docker compose exec -u postgres -T db pgbackrest ...`, **not** as root. `exec` defaults to root,
  which rewrites the repo's `archive.info` as root-owned `0640`; the WAL `archive_command` then runs
  as the postgres **server** uid (999) and can no longer read it, so all WAL archiving fails with
  `WAL segment ... not archived before the 60000ms timeout`. If a manual `pgbackrest` run as root has
  already broken it on a box, fix the ownership:
  ```bash
  sudo chown -R 999:999 $DOKTOK_BACKUP_DIR/pg
  ```
- **pg leg shows "stale" but WAL is fine.** The pg sentinel's freshness comes from
  `pg-wal-freshness.sh` (last archived WAL), not the base backup. If the leg ages, check that the
  `doktok-pg-wal-freshness.timer` is active (`systemctl list-timers 'doktok-*'`) and that
  `archive_timeout=60` is set on the db, so an idle DB still ships a segment each minute.

## Secrets

`DOKTOK_RESTIC_PASSWORD` and `DOKTOK_PGBACKREST_CIPHER_PASS` encrypt the repos - **store them off the
box** (a repo is useless without its key, and an SSD failure would take both). Azure credentials
(`DOKTOK_AZURE_SAS` / key) are write secrets. Names only in env examples; see the security runbook.
