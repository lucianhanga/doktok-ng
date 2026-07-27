#!/usr/bin/env bash
#
# Fetch the offsite backup repos from Azure into a local staging dir (#359) - the first half of
# "restore from Azure". Downloads one tarball pair (pg-repo + files-repo, written by
# deploy/azure-sync.sh), unpacks it into a staging layout that mirrors $DOKTOK_BACKUP_DIR
# (staging/pg + staging/files), and verifies both repos are readable. The SAME deploy/restore.sh
# then restores from staging (point DOKTOK_BACKUP_DIR at it) - into the live box or a staging
# target, without touching the live local repo.
#
# Usage:
#   ./deploy/azure-fetch.sh [staging-dir] [timestamp]
#     staging-dir  default ./backups.azure-restore
#     timestamp    the <ts> in pg-repo-<ts>.tar.gz (default: latest offsite set)
#
# Env: DOKTOK_AZURE_ACCOUNT, DOKTOK_AZURE_CONTAINER (required); auth via DOKTOK_AZURE_SAS, or
#      AZURE_STORAGE_KEY, or `az login`. Verification needs the compose db/backup-runner images.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

require az
require tar
: "${DOKTOK_AZURE_ACCOUNT:?set DOKTOK_AZURE_ACCOUNT}"
: "${DOKTOK_AZURE_CONTAINER:?set DOKTOK_AZURE_CONTAINER}"
trap 'err "azure fetch FAILED"; exit 1' ERR

staging="${1:-./backups.azure-restore}"
want_ts="${2:-}"
auth=()
[ -n "${DOKTOK_AZURE_SAS:-}" ] && auth+=(--sas-token "$DOKTOK_AZURE_SAS")

# Resolve the timestamp (latest set by default; names sort lexically by timestamp).
if [ -z "$want_ts" ]; then
    want_ts="$(az storage blob list --account-name "$DOKTOK_AZURE_ACCOUNT" \
        --container-name "$DOKTOK_AZURE_CONTAINER" --prefix "pg-repo-" \
        --query "[].name" -o tsv "${auth[@]}" | sort | tail -1 | sed -E 's/pg-repo-(.*)\.tar\.gz/\1/')"
fi
[ -n "$want_ts" ] || { err "no pg-repo-*.tar.gz found offsite - run an offsite sync first"; exit 1; }
echo "fetching offsite set ${want_ts} -> ${staging}"

mkdir -p "$staging"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"; err "azure fetch FAILED"; exit 1' ERR
for leg in pg files; do
    name="${leg}-repo-${want_ts}.tar.gz"
    az storage blob download --account-name "$DOKTOK_AZURE_ACCOUNT" \
        --container-name "$DOKTOK_AZURE_CONTAINER" --name "$name" \
        --file "$tmp/$name" "${auth[@]}" >/dev/null || { err "download failed: ${name} (exists offsite?)"; exit 1; }
    tar -xzf "$tmp/$name" -C "$staging"
    ok "unpacked ${name}"
done
rm -rf "$tmp"
trap 'err "azure fetch FAILED"; exit 1' ERR

# Verify both repos are readable (restic via the backup-runner; pgBackRest via the db image).
# Best-effort: a skipped check never blocks a fetch, a failed check does.
COMPOSE_FILES="${DOKTOK_COMPOSE_FILES:-docker-compose.prod.yml}"
COMPOSE_ENV_FILE="${DOKTOK_COMPOSE_ENV_FILE:-.env.production}"
compose=(docker compose)
for f in ${COMPOSE_FILES//,/ }; do
    compose+=(-f "$f")
done
compose+=(--env-file "$COMPOSE_ENV_FILE")
staging_abs="$(cd "$staging" && pwd)"

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

ok "offsite set ${want_ts} staged at ${staging}"
echo
echo "restore from it with:"
echo "  DOKTOK_BACKUP_DIR=${staging} ./deploy/restore.sh ./storage/files   # (+ optional PITR arg)"
echo "  (dev:  DOKTOK_BACKUP_DIR=${staging} make dev-restore FILES_TARGET=./storage/files)"
