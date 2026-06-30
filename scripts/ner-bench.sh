#!/usr/bin/env bash
#
# ner-bench.sh - benchmark the NER backends (current LLM vs local GLiNER vs local NuNER).
# Scores the labelled eval/corpus against eval/golden_entities.json.
# Requires for the 'current' backend: a running provider (Ollama up, or OpenAI key) + `make db`.
# Requires for gliner/nuner: `make ner-models` (installs gliner + rapidfuzz; first run downloads
# the models). Backends that are unavailable are skipped, not fatal.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
uv run python "$ROOT/scripts/_ner_bench.py"
