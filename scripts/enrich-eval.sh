#!/usr/bin/env bash
#
# enrich-eval.sh - run the local document-enrichment evaluation against real Ollama + DB.
# Ingests the golden corpus into a throwaway 'eval' tenant, runs doc_metadata + doc_classify,
# and scores title/date/location/summary/categories against eval/golden_enrichment.json.
# Requires: a running Ollama (with the configured models) and the local Postgres (make db).

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
uv run python "$ROOT/scripts/_enrich_eval.py"
