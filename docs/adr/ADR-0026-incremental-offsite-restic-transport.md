# ADR-0026: Incremental offsite backup via restic repos on Azure Blob

## Status

Accepted (2026-08-18 design, ticket #827; implemented the same day).

Amends the offsite half of the #766 tarball/GFS design (`deploy/azure-sync.sh` shipping
whole-repo tarballs into two WORM containers): the tarball transport is replaced, the container
immutability policies are dropped, and the sync cadence changes from daily to hourly. The full
design is in
[docs/superpowers/specs/2026-08-18-incremental-offsite-backup-design.md](../superpowers/specs/2026-08-18-incremental-offsite-backup-design.md);
the local backup engine (restic files repo + pgBackRest with WAL archiving) is unchanged.

## Context

The #766 offsite transport tars the **entire** local backup repo once per sync and keeps a GFS
rotation of those tarballs in Azure. Storage therefore grows with `corpus × retention classes`
instead of `corpus + churn`:

| corpus scale | local repos (incremental) | Azure, tarball transport (#766) | Azure, this ADR |
|---|---|---|---|
| 1× (today, 2.4 GB files / 29 MB db) | ~1.2 GB | ~10–14 GB | ~1.2 GB |
| 100× | ~50–70 GB | ~1–2 TB | ~50–70 GB |
| 1000× | ~0.4–1 TB | ~10–25 TB | ~0.4–1.1 TB |

(Sizing assumes a text-heavy corpus, ~5:1 restic compression observed 2026-08-18: 2.353 GiB →
412 MB; a scan-heavy corpus shifts the file-leg estimates ~4× up.)

The 2026-08-18 dev-box incident made the structural problems concrete: a misconfigured dev cron
(15-minute `TYPE=full`) grew the local pgBackRest repo to 9.3 GB against a 29 MB database, the
tarball transport amplified that to ~14 GB in Azure across the two containers, and **4.65 GB of
it was undeletable** because it sat inside a container WORM window. The incident showed two
things at once: whole-repo tarballs amplify any local mistake multiplicatively offsite, and
time-based WORM on the backup containers conflicts with any design that needs to prune offsite
data — restic `forget --prune` deletes pack, index, and lock files, which an immutability policy
forbids for the whole window.

## Decision

**Transport: restic end-to-end, hourly.** `deploy/azure-sync.sh` keeps two restic repos on Azure
Blob, created once by `deploy/azure-provision.sh`:

- `azure:<DOKTOK_AZURE_CONTAINER>:/files` — an hourly `restic backup` of the files tree. Same
  source as the local files repo ⇒ same chunk dedup; only churn crosses the wire. Deliberately a
  direct backup, not `restic copy` from the local repo: `copy --from-repo` cross-repo auth proved
  unreliable on restic 0.14 (spike 2026-08-18), and direct backup keeps every leg on single-repo
  auth. Revisit `copy` as an optimization at scale.
- `azure:<DOKTOK_AZURE_CONTAINER>:/pg` — an hourly `restic backup` of the local pgBackRest repo
  dir (excluding `log/` and `lock/`). pgBackRest repo files are write-once, so dedup is exact at
  file/chunk granularity: each run uploads only new WAL segments and new backup files. The pg WAL
  stream rides inside this leg, so the offsite pg recovery point tracks the local one within the
  sync cadence.

**Retention is snapshot metadata, not duplicate bytes.** After the copies, the same run executes
`restic forget --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --keep-yearly 1` on both
Azure repos. The GFS classes of #766 become restic snapshot selection over shared chunks; Azure
holds ~1× the compressed corpus plus churn.

**Cadence: daily → hourly.** Hourly uploads are tiny (incremental), so the cadence is affordable
and the offsite RPO becomes 1 h for both legs. Prod: `doktok-azure-sync.timer`
`OnCalendar=hourly`. Dev crontab: `47 3 * * *` → `7 * * * *`. Local RPOs are unchanged (pg ~60 s
WAL, files 15 min).

**Ransomware resistance moves from WORM to the access layer.** The time-based container
immutability policies are removed (they block prune — the 2026-08-18 incident). In their place:

- blob **soft-delete, 30 days**, at the storage-account level (plus the existing versioning);
- a **two-SAS split**: the hourly sync credential `DOKTOK_AZURE_SAS` is `rwcl` with **no delete**
  (its compromise cannot destroy backup history), while the delete-capable
  `DOKTOK_AZURE_SAS_PRUNE` (`rwcld`) is used only by the forget/prune step and is stored
  host-only (prod: `/etc/doktok/backup.env`; dev: `.env`).

**Restore stays two-stage.** `deploy/azure-fetch.sh` keeps its interface (fetch a set into a
staging dir; `TS=` now selects a snapshot timestamp instead of a blob name) but restic-restores
the Azure repos instead of downloading tarballs, rebuilding the local repo layout the existing
`restore.sh` / `restore-files.sh` / pgBackRest PITR path consumes unchanged.

**Rollback escape hatch.** The #766 tarball transport survives one release as
`deploy/azure-sync-tarball.sh`, selected with `DOKTOK_OFFSITE_TRANSPORT=tarball` (default
`restic`). Escape hatch only; no new logic. The legacy tarball sets already in Azure are not
migrated — the account lifecycle rule (rescoped to the `pg-repo-`/`files-repo-` prefixes, never
the restic prefixes) expires them, with the 2026-07-28 LTS sets becoming deletable when their
WORM window ends (~2026-08-27).

**Shelved option B: pgBackRest native Azure repo2.** Pointing pgBackRest's own `repo2` at Azure
(`repo1-type=azure`-style) was evaluated and explicitly shelved: it would make the offsite pg leg
native (WAL pushed continuously instead of hourly) but splits the offsite design across two
tools, re-opens per-repo cipher/retention mapping, and leaves the files leg on restic anyway.
Trigger to revisit: a real requirement for ~1-minute **offsite** pg currency. Until then the
hourly restic copy of the repo (with its WAL) meets the 1 h offsite RPO.

## Consequences

- Offsite uploads and storage become incremental: only new chunks cross the wire; Azure storage
  grows ~linearly with the data (± retention overlap) instead of with `corpus × classes`.
- Offsite RPO is 1 h for both legs; the DRP sentinel/history contract
  (`backups/status/offsite.json`, `history.jsonl`) is preserved byte-compatibly, so the DRP
  panel, watchdog, and drills are untouched.
- Two Azure credentials to provision instead of one; `azure-provision.sh` prints the exact SAS
  shapes, and the prune SAS must be treated as a host-only secret (it can destroy backup history;
  30-day soft-delete bounds the blast radius). **Soft-delete is not WORM** — this trades the hard
  guarantee for operability, accepted on 2026-08-18; re-evaluate if compliance requirements
  change.
- A lifecycle delete must never target the restic prefixes (`files/`, `pg/`) — it would corrupt
  the repos; the rule stays scoped to the legacy tarball prefixes.
- `make drp-selftest` gained a live offsite round-trip leg (seed → backup to a throwaway Azure
  prefix → restore → compare, pruned on teardown), skipped when the Azure env is absent; CI keeps
  covering the scripts by text contract.
- **Deviation from the spec: unconditional `unlock --remove-all` before each prune.** The spec's
  stale-lock sweep ("remove locks older than 2× the sync interval") became an unconditional
  `restic unlock --remove-all` before each `forget --prune`, because restic 0.14 leaves fresh
  orphan locks behind even with `--no-lock` (the no-delete sync SAS cannot remove the lock it
  creates) and `forget` refuses to run on any non-stale lock. This is safe only because sync and
  prune are sequential within one run and the scheduler (systemd oneshot / cron) never overlaps
  itself — so the operational rule is: never run a manual `make dev-azure-sync` concurrently with
  the timer/cron run.

Related: [ADR-0020](ADR-0020-hybrid-deployment-topology.md) (the deployment this offsite leg
protects), [backup-and-recovery.md](../operations/backup-and-recovery.md) (runbook), #766 (the
amended tarball design), #827 (this change).
