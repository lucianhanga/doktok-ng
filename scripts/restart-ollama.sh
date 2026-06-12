#!/usr/bin/env bash
#
# Restart the local Ollama server with the settings DokTok NG needs:
#   - several requests in parallel and several models resident at once
#   - a quantized KV cache + flash attention so the 32k-context chat model leaves
#     room for the small embedding model (otherwise chunk_embed hangs)
# Then pre-pin the embedding model so it is loaded first and never evicted.
#
# Usage:   ./scripts/restart-ollama.sh
# Override any setting via the environment, e.g.:
#   OLLAMA_MAX_LOADED_MODELS=6 EMBED_MODEL=qwen3-embedding:0.6b ./scripts/restart-ollama.sh
#
set -euo pipefail

# ---- settings (override by exporting before calling) -------------------------------------------
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-4}"
export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-4}"
export OLLAMA_FLASH_ATTENTION="${OLLAMA_FLASH_ATTENTION:-1}"
export OLLAMA_KV_CACHE_TYPE="${OLLAMA_KV_CACHE_TYPE:-q8_0}"
OLLAMA_HOST_URL="${OLLAMA_HOST_URL:-http://localhost:11434}"
OLLAMA_LOG="${OLLAMA_LOG:-$HOME/.ollama/serve.log}"
EMBED_MODEL="${EMBED_MODEL:-qwen3-embedding:0.6b}"
READY_TIMEOUT="${READY_TIMEOUT:-30}"

# ---- colours: green = success, red = failure, yellow = warning -------------------------------
GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; YELLOW=$'\033[0;33m'; NC=$'\033[0m'
ok()   { printf '%s%s%s\n' "$GREEN" "$1" "$NC"; }
warn() { printf '%s%s%s\n' "$YELLOW" "$1" "$NC"; }
err()  { printf '%s%s%s\n' "$RED" "$1" "$NC" >&2; }

command -v ollama >/dev/null 2>&1 || { err "ollama not found on PATH"; exit 1; }

# ---- stop any running server -----------------------------------------------------------------
if pgrep -f "ollama serve" >/dev/null 2>&1; then
  warn "Stopping existing ollama serve ..."
  pkill -f "ollama serve" || true
  for _ in $(seq 1 10); do
    pgrep -f "ollama serve" >/dev/null 2>&1 || break
    sleep 1
  done
  if pgrep -f "ollama serve" >/dev/null 2>&1; then
    warn "Still running; sending SIGKILL ..."
    pkill -9 -f "ollama serve" || true
    sleep 1
  fi
  ok "Stopped."
else
  warn "No ollama serve was running."
fi

# ---- start with the settings -----------------------------------------------------------------
mkdir -p "$(dirname "$OLLAMA_LOG")"
warn "Starting ollama serve  (NUM_PARALLEL=$OLLAMA_NUM_PARALLEL  MAX_LOADED_MODELS=$OLLAMA_MAX_LOADED_MODELS  FLASH_ATTENTION=$OLLAMA_FLASH_ATTENTION  KV_CACHE_TYPE=$OLLAMA_KV_CACHE_TYPE)"
nohup ollama serve >"$OLLAMA_LOG" 2>&1 &
serve_pid=$!

# ---- wait until the API answers --------------------------------------------------------------
ready=false
for _ in $(seq 1 "$READY_TIMEOUT"); do
  if curl -sf "$OLLAMA_HOST_URL/api/version" >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if [ "$ready" != true ]; then
  err "Ollama did not become ready within ${READY_TIMEOUT}s. Check $OLLAMA_LOG"
  exit 1
fi
ver=$(curl -s "$OLLAMA_HOST_URL/api/version" | sed 's/[{}\"]//g')
ok "Ollama is up (pid $serve_pid, $ver). Logs: $OLLAMA_LOG"

# ---- pre-pin the embedding model so it loads first and stays resident ------------------------
warn "Pre-loading the embedding model ($EMBED_MODEL, pinned) ..."
if curl -sf "$OLLAMA_HOST_URL/api/embed" \
    -d "{\"model\":\"$EMBED_MODEL\",\"input\":\"warmup\",\"keep_alive\":\"-1\"}" >/dev/null 2>&1; then
  ok "Embedding model resident and pinned."
else
  warn "Could not pre-load the embedding model (it will load on first use)."
fi

# ---- show what is resident -------------------------------------------------------------------
loaded=$(curl -s "$OLLAMA_HOST_URL/api/ps" \
  | python3 -c 'import sys,json; print(", ".join(m["name"] for m in json.load(sys.stdin).get("models",[])) or "(none)")' 2>/dev/null || echo "?")
ok "Resident models: $loaded"
