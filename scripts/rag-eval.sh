#!/usr/bin/env bash
#
# rag-eval.sh - run the local RAG evaluation harness against real Ollama + DB.
# Ingests the golden corpus into a throwaway 'eval' tenant and scores the golden Q/A set.
# Requires: a running Ollama (with the configured models) and the local Postgres (make db).

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
uv run python "$ROOT/scripts/_rag_eval.py"
