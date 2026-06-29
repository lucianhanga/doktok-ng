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
- `core/doktok_core/knowledge_graph/evaluation.py` — pure metric logic for the KAG **graph-quality**
  tracks (`score_edges` for edge precision/recall/F1, `evaluate_provenance` for evidence validity),
  unit-tested in CI with synthetic inputs (no models).
- `eval/golden_edges.json` — the gold relationship triples over the corpus
  (`{subject, predicate, object, source}`), the ground truth for edge precision/recall.
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

### Eval model (the live system configuration)

`make rag-eval` runs on **exactly the live system AI configuration** (Settings → AI, DB-backed) —
**no fallback**: the **pipeline** purpose drives NER/relation extraction and the **interrogation
(RAG)** purpose drives chat/answering, each on its configured provider (OpenAI *or* local Ollama).
So if the system is set to OpenAI, the eval runs on OpenAI; the benchmark always reflects what
production actually runs. If a purpose is set to OpenAI but the key/egress isn't usable, the run
**aborts loudly** rather than silently substituting a local model.

The run prints an **environment banner** first, e.g.:

```
=== Eval environment ===
  extraction (pipeline): OpenAI gpt-4o-mini
  chat (interrogation):  OpenAI gpt-4o-mini
  embedding:             Ollama qwen3-embedding:0.6b
  no-egress:             False
  chat mode:             agent
  tenant:                eval
```

To A/B a different model, change the model in Settings → AI (there is no env override — the eval
uses only what is configured). Embeddings always use the real `DOKTOK_EMBEDDING_MODEL` (the index
has to match what production queries against).

### Chat mode (`DOKTOK_EVAL_CHAT_MODE`)

By default the golden set is scored through the **classic** deterministic RAG answerer. Set
`DOKTOK_EVAL_CHAT_MODE` to benchmark the agentic paths (ADR-0022) with the identical metrics, so you
can see whether they actually beat classic on counting/relational/aggregation cases (and at what
cost):

```bash
DOKTOK_EVAL_CHAT_MODE=agent make rag-eval   # the single-agent tool-calling loop
DOKTOK_EVAL_CHAT_MODE=multi make rag-eval   # the LangGraph plan/gather/merge/critic graph
```

`agent`/`multi` need a tool-calling model. They use the eval model — by default the configured
`DOKTOK_DEFAULT_MODEL`; if its tool-calling is unreliable (e.g. the local MoE), pin a dense model for
the run with `DOKTOK_EVAL_MODEL=qwen3:14b`. These modes run several model calls per case, so they are
markedly slower than `classic`.

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

## Graph-quality tracks (KAG)

The relational track above measures end-to-end *answers*. Two further tracks measure the **graph
itself** — the relation extractor and its edge evidence — printed by the runner from the
already-built `eval`-tenant graph (no rebuild). The pure scoring lives in
`core/doktok_core/knowledge_graph/evaluation.py` and is unit-tested in CI.

### Edge precision / recall (`=== KG edge quality ===`)

Scores the extracted `kg_edges` against `eval/golden_edges.json` (the gold relationship triples).
A match is on the normalized `(subject, predicate, object)` key (`normalize_ner_name` for the
endpoints, upper-cased predicate — type-aware via the predicate), so casing/whitespace differences
do not count as misses. The runner reports **precision / recall / F1 overall and per-predicate**,
plus the **missed gold edges** (recall failures — the relationship is true but the extractor did not
produce it) and the **spurious extracted edges** (precision failures — the extractor invented or
mis-typed a relationship), so a low score is diagnosable. The gold set is kept deliberately small and
**unambiguously true** (the three explicitly-stated household relationships); a borderline
relationship (e.g. an org's office location) is intentionally excluded rather than risk a wrong gold.

### Provenance correctness (`=== Provenance correctness ===`)

For every edge, checks that its `kg_edge_provenance` evidence is **trustworthy**: the evidence span
is non-empty, is a (whitespace-normalized) substring of the cited source document's text, and
contains both endpoint surface forms. Reports the **valid-evidence rate** and lists the offending
edges with the reason (empty evidence / not found in the document / endpoint missing from the span).
This guards the citation contract: a graph-grounded answer cites the provenance document, so evidence
that does not actually support the edge would be a silent grounding failure.

Both tracks need the graph, which needs a live Ollama for the relation extractor, so they only run
inside the local `make rag-eval`. A weak extractor surfaces here as low recall (missed edges) or low
precision (spurious edges) — exactly the signal to hand back to the model/extractor work.
