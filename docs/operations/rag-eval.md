# RAG evaluation harness

A small, deterministic harness to measure RAG quality so every change (embeddings, reranking, prompt
tweaks) is **measured, not guessed**.

## What it measures

For each golden case it computes:

- **retrieval recall** — did an expected source document appear in the top-k retrieval?
- **answer correctness** — does the grounded answer contain the expected fact(s)?
- **citation correctness** — does a citation point at an expected source?
- **refusal correctness** — for out-of-scope questions, did it correctly refuse?

A case `passes` if (refusal cases) it refused, or (answerable cases) it was grounded, contained the
expected text, and cited an expected source.

## Pieces

- `core/doktok_core/rag/evaluation.py` — pure metric logic (`evaluate(cases, retriever, answerer)`),
  unit-tested in CI with fakes (no models).
- `eval/corpus/` + `eval/golden.json` — a tiny golden corpus and Q/A set. Cases are tagged by `kind`
  (`factoid`, `aggregation`, `refusal`) so the report breaks down by type.
- `scripts/_rag_eval.py` (`make rag-eval`) — the **local** runner: ingests the corpus into a throwaway
  `eval` tenant, indexes it with the real embedding model, runs the golden set against the real hybrid
  retriever + RAG answerer, and prints a per-case + aggregate report. Needs a running Ollama and DB
  (`make db`); it is not run in CI.

## Running it

```bash
make db            # if not already up
make rag-eval      # ingests the corpus, runs the golden set, prints the report
make enrich-eval   # ingests + enriches the corpus, scores title/date/location/category/summary
```

## Enrichment eval (M6.2)

`make enrich-eval` (`scripts/_enrich_eval.py`) ingests the same corpus into a throwaway `eval` tenant,
runs the `doc_metadata` + `doc_classify` features against the real models, and scores the produced
fields against `eval/golden_enrichment.json`: **title** (non-empty, not the filename stem, expected
keyword), **document_date** (matches expected ISO date, or NULL when `n/a` — no hallucinated dates),
**location**, **categories** (at least one matches), and **summary** present. The pure scoring
(`core/doktok_core/enrichment/evaluation.py`) is unit-tested in CI; the runner needs Ollama and is
local-only. Baseline: 4/4 documents pass all checks.

## Note on aggregation cases (the "beyond-RAG" gap)

The golden set deliberately includes an **aggregation** case ("how much did I spend at Block House in
total?"). On a *small* corpus this passes — all the matching transactions fit in the retrieved chunks
and the 32k context window, so the model sums them correctly. The gap that motivates the structured
extraction + deterministic aggregation track (see
`~/.claude/agent-memory/agentic-ai-architect/project_doktok-structured-aggregation.md`) appears at
**scale**: when there are many statements and top-k retrieval can no longer fetch *every* matching
transaction, similarity search misses some and the LLM mis-sums the rest. To exercise that gap, grow
the aggregation fixtures (e.g. a year of statements with many transactions each) so the relevant lines
exceed what top-k retrieval returns.
