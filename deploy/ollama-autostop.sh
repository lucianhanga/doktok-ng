#!/usr/bin/env bash
#
# Reclaim the in-stack Ollama container's memory when it is unused (M16 #374). Reads the backend's
# "is local Ollama needed?" flag and reconciles the container state: stop it when every Ollama
# consumer is offloaded (remote URL / OpenAI, and OCR not on Ollama-vision), start it when one is
# local again. The app never touches Docker itself (no docker.sock in a container) - this runs on the
# host, e.g. from a systemd timer every ~2 minutes. Run from the repo checkout (docker-compose.prod.yml).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

COMPOSE=(docker compose -f docker-compose.prod.yml --env-file .env.production)
BASE_URL="${DOKTOK_BASE_URL:-http://localhost}"   # via Caddy, which injects the API token

needed="$(curl -fsS "${BASE_URL}/api/v1/settings/ollama-status" 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["local_ollama_needed"])' 2>/dev/null || true)"
if [ -z "$needed" ]; then
    warn "ollama-autostop: could not read ollama-status (backend down?); leaving the container as-is"
    exit 0
fi

running="$(docker inspect -f '{{.State.Running}}' doktok-prod-ollama 2>/dev/null || echo false)"

if [ "$needed" = "True" ]; then
    if [ "$running" != "true" ]; then
        "${COMPOSE[@]}" start ollama >/dev/null && ok "local Ollama needed -> started the container"
    else
        ok "local Ollama needed -> already running"
    fi
else
    if [ "$running" = "true" ]; then
        "${COMPOSE[@]}" stop ollama >/dev/null && ok "local Ollama unused -> stopped (memory reclaimed)"
    else
        ok "local Ollama unused -> already stopped"
    fi
fi
