# ADR-0023: Pluggable NER and relation backends

## Status

Accepted (GLiNER / GLiNER-Relex adapters + benchmarks shipped, opt-in; Settings-UI selection planned).

## Context

KAG enrichment has two model-driven steps behind ports in `doktok_contracts`:

- **NER** (`EntityNerExtractor.extract(text) -> [ExtractedEntity]`) — PERSON / ORG / GPE occurrences.
- **Relations** (`RelationExtractor.extract(text, entity_list) -> [ExtractedRelation]`) — directed
  triples over the closed predicate vocabulary in `core/doktok_core/knowledge_graph/predicates.py`.

Until now both ran **only on the configured pipeline LLM** (OpenAI `gpt-4o-mini` remote, or an
Ollama model local). That works but has costs: the remote path moves document text off-host (an
egress concern, ADR-0020) and is the slowest step in enrichment; the local LLM path is heavy. The
ports already make these swappable in principle. This ADR records making that first-class by adding
**local, purpose-built span models** as selectable backends, and the decision on which to recommend.

Candidate implementations evaluated:

| Capability | Backend | Kind | Model |
|---|---|---|---|
| NER | OpenAI LLM | remote | `gpt-4o-mini` |
| NER | Ollama LLM | local | `qwen3:14b`, `qwen3.6:27b` |
| NER | **GLiNER** | local | `gliner-community/gliner_large-v2.5` |
| NER | NuNER | local | `numind/NuNER_Zero` |
| Relations | OpenAI / Ollama LLM | remote / local | pipeline model |
| Relations | **GLiNER-Relex** | local | `knowledgator/gliner-relex-large-v1.0` |

GLiNER / NuNER / GLiNER-Relex are wrapped in the `doktok-provider-gliner` package (the upstream
refiner libraries vendored, plus thin adapters that map to the doktok ports). GLiNER-Relex does
joint entity+relation extraction; its adapter grounds relations to the document `entity_list` and
maps the model's open-vocabulary predicates back onto the closed vocabulary, with direction
correction and type validation against `PREDICATE_TYPE_PAIRS` (the single source of truth).

## Benchmark

`make ner-bench` scores each NER backend on the labelled `eval/corpus` (gold in
`eval/golden_entities.json`); `make kg-bench` does the same for relations, grounding every backend on
the same gold entities so it isolates relation quality. Both report exact + relaxed P/R/F1 (relaxed =
token-containment match), per-type / per-predicate F1, entity/edge counts, and latency.

NER results (7 docs, OpenAI `gpt-4o-mini` as the live pipeline; GLiNER/NuNER on CPU):

```
backend                       exact P  exact R  exact F1  relax F1   ents   ms/doc
----------------------------------------------------------------------------------
current (OpenAI gpt-4o-mini)     78.9     78.9      78.9      94.7     19   1114.5  *
gliner (gliner_large-v2.5)       66.7     63.2      64.9      81.1     18    121.6
nuner  (NuNER_Zero)              15.2     36.8      21.5      52.3     46    125.8

Per-type exact F1                  GPE       ORG    PERSON
current (OpenAI gpt-4o-mini)      88.9      69.6     100.0
gliner (gliner_large-v2.5)        40.0      70.0      85.7
nuner  (NuNER_Zero)               42.9      20.5       0.0
```

Reading of the numbers:

- **OpenAI is the most accurate** (94.7 relaxed F1, perfect on PERSON) but **~9× slower** per doc
  (1.1 s vs ~0.12 s) and sends text off-host.
- **GLiNER is the strong local option**: 81.1 relaxed F1 at ~122 ms/doc, fully on-host, competitive
  on ORG (70.0) and PERSON (85.7); its main weakness is GPE (40.0).
- **NuNER is not viable**: 21.5 exact / 52.3 relaxed F1, 0.0 on PERSON, and over-produces (46
  entities vs ~19 gold) — high noise, low precision.
- **Local LLMs (qwen3:14b / qwen3.6:27b) were dropped from the comparison**: NER runs the model over
  every text window, which on these dense/MoE models takes minutes per document — too slow to be a
  practical enrichment backend.

Relation results (7 docs, 3 gold edges; both backends grounded on the same gold entities):

```
backend                       P      R     F1  edges   ms/doc
-------------------------------------------------------------
current (OpenAI gpt-4o-mini)  27.3  100.0  42.9     11   1770.3
gliner-relex (relex-large-v1) 50.0   66.7  57.1      4   1163.2  *

Per-predicate F1              BANKS_WITH  CUSTOMER_OF  EMPLOYED_BY  INSURED_BY  RESIDES_IN
current (OpenAI gpt-4o-mini)       100.0          0.0        100.0       100.0         0.0
gliner-relex (relex-large-v1)      100.0          0.0        100.0         0.0         -
```

Reading of the numbers:

- **GLiNER-Relex wins on F1** (57.1 vs 42.9) — and is **faster** (~1.2 s vs ~1.8 s/doc) and local.
- **The LLM has higher recall (100%) but poor precision (27.3%)**: it floods spurious edges — every
  amex merchant as `CUSTOMER_OF American Express`, plus `Johanna Mertens CUSTOMER_OF/RESIDES_IN` — so
  it finds every gold edge but buries them in noise (11 edges for 3 gold).
- **GLiNER-Relex is tighter (4 edges, precision 50%)** but missed one gold edge (`Johanna Mertens
  INSURED_BY Allianz` — it emitted `CUSTOMER_OF` there instead), hence recall 66.7%.
- Both whiff on `CUSTOMER_OF` (0.0) — neither maps the insurer/bank relations to that predicate; a
  predicate-surface tuning item, not a backend choice.

## Decision

Offer two backends per capability and make them selectable; pick the default from the benchmark.

**NER** — default **remote → OpenAI** (`gpt-4o-mini`, the accuracy leader at 94.7 relaxed F1);
**local → GLiNER** (`gliner_large-v2.5`) is the recommended on-host alternative (~9× faster, no
egress, real offsets, accuracy close enough for KAG). NuNER and the local Ollama LLMs are **not
offered** (accuracy / latency above); NuNER stays only as a benchmark comparison point.

**Relations** — default **local → GLiNER-Relex** (`knowledgator/gliner-relex-large-v1.0`): it has
the **best F1**, is faster, and needs no egress; **remote → OpenAI** is the alternative (higher
recall, but low precision from spurious edges). This is the one capability where the local model is
the better default on the current corpus.

Selection is opt-in via environment for now (folded into the worker's rebuild signature):

```
DOKTOK_NER_BACKEND=gliner          # local NER       (default: the configured LLM NER)
DOKTOK_REL_BACKEND=gliner-relex    # local relations (recommended; default falls back to the LLM)
DOKTOK_NER_DEVICE=cuda             # optional torch device (default cpu)
```

The runtime default stays the configured LLM until the local model is installed (`make ner-models`);
once installed, GLiNER-Relex is the recommended setting for relations per the benchmark above.

A Settings-UI selector (local / remote, mirroring the OCR-engine selector of ADR-0021) is the
planned follow-up. The local runtime is installed out-of-band: `make ner-models`.

## Consequences

- A new opt-in capability with **no default behavior change** — unset env = today's LLM path.
- The local GLiNER path needs **no egress**, so it is usable under the no-egress posture (ADR-0020)
  where the remote LLM is blocked.
- Heavy deps (`gliner`, `torch`) are an optional extra (`doktok-provider-gliner[engine]`), not in the
  default install; the package imports lightly and loads a model only when instantiated.
- The benchmarks (`make ner-bench`, `make kg-bench`) are reproducible gates for any future backend or
  model change. See [docs/operations/rag-eval.md](../operations/rag-eval.md).
- Vendored upstream refiner code is typed loosely (mypy `ignore_errors` for the vendored subpackages);
  the adapters are the strictly-typed, supported boundary.

Related: [ADR-0021](ADR-0021-pluggable-ocr-engines-and-device-aware-recommendation.md) (the same
pluggable-capability pattern for OCR), [ADR-0022](ADR-0022-agentic-chat-with-tools.md),
[ADR-0020](ADR-0020-hybrid-deployment-topology.md) (egress posture).
