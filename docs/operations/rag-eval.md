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
  (`factoid`, `aggregation`, `refusal`, `conversation`, `relational`) so the report breaks down by
  type. A `conversation` case carries a `history` (prior turns); it is answered via `answer_thread`
  so the follow-up `question` is rewritten against that history, and retrieval recall is measured
  against the rewritten query (M6.4 multi-turn). A `relational` case is a multi-hop / cross-document
  relationship question answered with the KAG graph-augmented retriever (see below).
- `scripts/_rag_eval.py` (`make rag-eval`) — the **local** runner: ingests the corpus into a throwaway
  `eval` tenant, indexes it with the real embedding model, **builds the KAG knowledge graph** over
  the eval tenant (the entities → ner → entity_graph → relations feature chain + alias folding, using
  the real Ollama NER + relation extractors), wires a `DefaultGraphRetriever` into the answerer, runs
  the golden set, and prints a per-case + aggregate report. Needs a running Ollama and DB
  (`make db`); it is not run in CI. The `eval`-tenant `kg_*` rows are cleaned up alongside the rest.

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

## Relational track (KAG, Phase 3)

The golden set includes **relational** cases — multi-hop / cross-document relationship questions
("who is Johanna Mertens insured by?", "what organisations is Stefan Vogel connected to?") over the
household corpus (`insurance-policy.txt`, `bank-welcome.txt`, `employment-letter.txt`), each with a
clear, explicitly-stated relationship matching the closed predicate vocabulary
(`INSURED_BY` / `BANKS_WITH` / `EMPLOYED_BY`).

These measure the gain from graph-augmented retrieval. On relational questions the answerer's
deterministic gate (`looks_relational`) fires, links the question's entities to canonical graph
nodes, traverses a bounded neighborhood / path, and fuses the cross-document relationship — with its
source document as a citation — into the grounded answer. On this *tiny* corpus a single-document
relationship may also be answerable by pure RAG; the gap the graph closes shows on the
**cross-document** case (e.g. Stefan Vogel's bank *and* employer live in two separate letters, which
top-k retrieval may not surface together) and grows with corpus size. The graph is only as good as
the extracted edges, so a weak relation-extraction model will show up here as missed relational
cases — that is the point of measuring it.
