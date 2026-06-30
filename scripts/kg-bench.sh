#!/usr/bin/env bash
#
# kg-bench.sh - benchmark KG relation extraction (current LLM vs local GLiNER-Relex).
# Both backends are grounded on the gold entities, then scored against eval/golden_edges.json.
# Requires for 'current': a running provider (Ollama up, or OpenAI key) + `make db`.
# Requires for gliner-relex: `make ner-models` (installs gliner; first run downloads the relex
# model). Backends that are unavailable are skipped, not fatal.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
uv run python "$ROOT/scripts/_kg_bench.py"
