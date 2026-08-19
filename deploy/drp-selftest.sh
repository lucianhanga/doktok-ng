#!/usr/bin/env bash
#
# One-command, NO-RISK DRP self-test (Tier 1). Proves on THIS machine that:
#   1. Postgres point-in-time recovery works   (deploy/test-pitr.sh)
#   2. a portable backup made on one instance restores onto a fresh one, with pgvector + files
#      and through the same AES-256 passphrase encryption   (deploy/restore-roundtrip.sh)
# Both run entirely in throwaway containers + temp dirs - they touch NO real database or files.
# Requires Docker. The full app-level + systemd-triggered restore is the Tier-3 test on a real box.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh
require docker

echo "=== DRP self-test 1/2: Postgres PITR proof ==="
./deploy/test-pitr.sh
ok "PITR proof PASSED"

echo
echo "=== DRP self-test 2/2: portable backup export -> restore round-trip ==="
./deploy/restore-roundtrip.sh

echo
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
    # teardown needs delete rights: prune SAS when present; without it the throwaway prefix's
    # repo skeleton (config/keys, a few KBs) stays behind - the lifecycle rule is scoped to the
    # legacy pg-repo-/files-repo- prefixes and will NOT clean it - until someone prunes it with
    # delete rights.
    if [ -n "${DOKTOK_AZURE_SAS_PRUNE:-}" ]; then
        offsite_azure_env prune
        RESTIC_REPOSITORY="azure:${DOKTOK_AZURE_CONTAINER}:/$st_id" \
            offsite_restic forget --prune --keep-last 0 >/dev/null 2>&1 || true
    fi
    rm -rf "$st_payload" "$st_out"
else
    warn "offsite round-trip skipped (no DOKTOK_AZURE_* env)"
fi

echo
ok "DRP self-test PASSED (PITR + portable export/restore round-trip)"
