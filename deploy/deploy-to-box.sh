#!/usr/bin/env bash
#
# One-shot deploy of the current working tree to a remote compose box (M11/M12). Rsyncs the source,
# rebuilds the images on the box with live build progress, recreates the changed services, and shows
# the resulting health. Idempotent and safe to re-run.
#
# Config (env vars, all have defaults so a bare `make deploy-box` works):
#   DOKTOK_BOX_HOST   ssh target           (default lh@10.0.0.70)
#   DOKTOK_BOX_KEY    ssh private key path  (default the on-prem N95 key)
#   DOKTOK_BOX_DIR    remote deploy dir     (default /opt/doktok)
#   DOKTOK_BOX_SERVICES  images to rebuild  (default "backend caddy worker backup-runner")
#   DOKTOK_BOX_NO_BUILD=1  skip the rebuild (just rsync + up -d; for deploy/*.sh-only changes)
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh  # ok()/warn()/err() colour helpers: green=success, red=failure, yellow=warning

HOST="${DOKTOK_BOX_HOST:-lh@10.0.0.70}"
KEY="${DOKTOK_BOX_KEY:-$HOME/Library/CloudStorage/OneDrive-Personal/Documents/paperless/ssh.keys/onprem.paperless.hostT0003.pem}"
DIR="${DOKTOK_BOX_DIR:-/opt/doktok}"
SERVICES="${DOKTOK_BOX_SERVICES:-backend caddy worker backup-runner}"
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.production"

[ -f "$KEY" ] || {
    err "ssh key not found: $KEY (set DOKTOK_BOX_KEY)"
    exit 1
}

warn "deploying $(git rev-parse --short HEAD 2>/dev/null || echo 'working tree') -> ${HOST}:${DIR}"

# 1. Sync source. Ship code only; never clobber the box's secrets (.env.production) or its document
#    store (storage/files). storage/ itself MUST ship (storage/{filesystem,postgres} are packages).
ok "[1/4] rsync source"
rsync -az \
    --exclude '.git' --exclude 'node_modules' --exclude '.venv' --exclude 'dist' \
    --exclude '__pycache__' --exclude '*.pyc' --exclude 'storage/files' \
    --exclude '.DS_Store' --exclude '.env.production' --exclude '.claude' \
    -e "ssh -i $KEY" ./ "${HOST}:${DIR}/"

# 2. Rebuild images on the box with live BuildKit progress (ssh -t = a TTY so the progress renders).
if [ "${DOKTOK_BOX_NO_BUILD:-0}" = "1" ]; then
    warn "[2/4] build skipped (DOKTOK_BOX_NO_BUILD=1)"
else
    ok "[2/4] rebuild images on box: ${SERVICES}"
    ssh -t -i "$KEY" "$HOST" "cd '$DIR' && $COMPOSE build ${SERVICES}"
fi

# 3. Apply: up -d recreates only services whose image or config actually changed.
ok "[3/4] recreate changed services"
ssh -i "$KEY" "$HOST" "cd '$DIR' && $COMPOSE up -d"

# 4. Show health.
ok "[4/4] stack status"
ssh -i "$KEY" "$HOST" "cd '$DIR' && $COMPOSE ps"

ok "deploy complete -> ${HOST}:${DIR}"
