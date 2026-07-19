#!/usr/bin/env bash
#
# preflight.sh - provision ALL local model-stack resources a service could select, before it
# starts. Runs as a prerequisite of `make run-backend` / `make run-worker`.
#
#   - Idempotent: only installs/pulls what is missing (uv pip install + ollama pull both skip
#     work that is already present, so a warm run is quick with no re-downloads).
#   - Per-service scope: each service provisions only the runtimes/models it actually uses. The
#     resource list is derived from doktok_core's MODEL_CATALOG + config defaults via the helper
#     scripts/_preflight_models.py (no hardcoded model ids here). Remote OpenAI options are
#     egress-gated and never pulled.
#   - Non-fatal externals: a missing Ollama daemon/CLI or a Hugging Face download hiccup prints a
#     YELLOW warning and CONTINUES - those can be fetched later. Only a genuine pip install error
#     (a real setup failure) surfaces and stops the run target.
#
# Escape hatch: DOKTOK_SKIP_PREFLIGHT=1 skips everything and exits 0 immediately.
#
# Usage: scripts/preflight.sh <backend|worker>

set -euo pipefail

# Color codes: green=success, red=failure, yellow=warning. Disabled when not a TTY.
if [ -t 1 ]; then
  GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; YELLOW=$'\033[0;33m'; NC=$'\033[0m'
else
  GREEN=''; RED=''; YELLOW=''; NC=''
fi
info() { printf '%s\n' "${GREEN}[preflight]${NC} $*"; }
warn() { printf '%s\n' "${YELLOW}[preflight] WARNING:${NC} $*" >&2; }
err()  { printf '%s\n' "${RED}[preflight] ERROR:${NC} $*" >&2; }

SERVICE="${1:-}"
case "$SERVICE" in
  backend | worker) ;;
  *)
    err "usage: $0 <backend|worker>"
    exit 2
    ;;
esac

# Escape hatch: skip the whole preflight (e.g. offline, or you manage the models yourself).
if [ "${DOKTOK_SKIP_PREFLIGHT:-0}" = "1" ]; then
  info "DOKTOK_SKIP_PREFLIGHT=1 - skipping model-stack preflight for '$SERVICE'."
  exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
MAKE="${MAKE:-make}"

# F-40 (#652): the .env carries secrets (DOKTOK_SECRETS_KEY, the DB DSN, tenant tokens) - warn
# when it is group/other-readable (non-fatal, like the other external warnings here).
if [ -f .env ] && [ -n "$(find .env -perm -044 2>/dev/null)" ]; then
  warn ".env is group/other-readable and contains secrets - restrict it: chmod 600 .env"
fi

info "Provisioning local model-stack resources for '$SERVICE' (idempotent; set DOKTOK_SKIP_PREFLIGHT=1 to skip)."

# Resolve the per-service resource plan from the catalog/config (single source of truth).
PLAN="$(uv run --no-sync python "$ROOT/scripts/_preflight_models.py" "$SERVICE")"

pip_targets=()
ollama_models=()
hf_repos=()
while IFS= read -r line; do
  [ -z "$line" ] && continue
  kind="${line%% *}"
  id="${line#* }"
  case "$kind" in
    pip) pip_targets+=("$id") ;;
    ollama) ollama_models+=("$id") ;;
    hf) hf_repos+=("$id") ;;
    *) warn "ignoring unrecognized plan line: $line" ;;
  esac
done <<< "$PLAN"

# 1) Python runtime extras. Reuse the Makefile targets (which own the package lists). These are
#    idempotent (uv pip install is a no-op when satisfied) - but a REAL install failure MUST stop
#    the run target, so this step is intentionally NOT wrapped in continue-on-error.
if [ "${#pip_targets[@]}" -gt 0 ]; then
  info "Python runtime extras: ${pip_targets[*]}"
  for t in "${pip_targets[@]}"; do
    info "\$ $MAKE $t"
    "$MAKE" "$t"
  done
fi

# 2) Ollama models. Idempotent (pull skips layers already present). A missing CLI or an
#    unreachable daemon is a WARNING, not a failure - the models can be pulled later.
if [ "${#ollama_models[@]}" -gt 0 ]; then
  if ! command -v ollama > /dev/null 2>&1; then
    warn "the 'ollama' CLI is not installed - skipping model pulls: ${ollama_models[*]}. Install Ollama and pull them later."
  elif ! ollama list > /dev/null 2>&1; then
    warn "the Ollama daemon is unreachable - skipping model pulls: ${ollama_models[*]}. Start Ollama and re-run to pull them."
  else
    for m in "${ollama_models[@]}"; do
      info "\$ ollama pull $m  (skips layers already present)"
      if ! ollama pull "$m"; then
        warn "could not pull '$m' - continuing. Pull it later with: ollama pull $m"
      fi
    done
  fi
fi

# 3) Hugging Face weights. Prefetch so the first real request is not cold. Any download failure
#    (offline, transient network) is a WARNING - the weights download on first use anyway.
if [ "${#hf_repos[@]}" -gt 0 ]; then
  for repo in "${hf_repos[@]}"; do
    info "\$ prefetch Hugging Face weights: $repo"
    if ! uv run --no-sync python -c \
      'import sys; from huggingface_hub import snapshot_download; snapshot_download(sys.argv[1])' \
      "$repo"; then
      warn "could not prefetch '$repo' - continuing (it downloads on first use)."
    fi
  done
fi

info "Preflight complete for '$SERVICE' - all local model-stack resources present. Any Ollama/HF items warned above can be fetched later."
