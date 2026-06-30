# doktok-provider-gliner (experimental)

Local GLiNER-family models adapted to doktokNG's enrichment ports, as alternatives to the LLM used
in data enrichment:

- **NER** (`EntityNerExtractor`) — GLiNER / NuNER span extraction (PERSON / ORG / GPE).
- **Relations** (`RelationExtractor`) — GLiNER-Relex joint entity+relation extraction, grounded to
  doktokNG's closed predicate vocabulary (KAG).

## Layout

- `doktok_provider_gliner/refiners/` — vendored upstream NER refinement pipeline (GLiNER/NuNER span
  candidates -> thresholds -> regex rules -> gazetteers -> dedup/overlap). Kept verbatim.
- `doktok_provider_gliner/kg_refiners/` — vendored upstream KAG pipeline (GLiNER-Relex joint
  extraction -> entity linking -> rule relations -> triple refinement -> optional LLM fallback).
- `doktok_provider_gliner/adapter.py` — `GlinerEntityNerExtractor` / `NuNerEntityNerExtractor`:
  window the document, map `person|organization|location` -> `PERSON|ORG|GPE`, emit
  `ExtractedEntity` with real character offsets, de-duplicate on `(type, normalized)`.
- `doktok_provider_gliner/relation_adapter.py` — `GlinerRelexRelationExtractor`: ask GLiNER-Relex
  for natural-language phrasings of the closed predicates, then map each relation back to the
  canonical predicate, ground both endpoints to the supplied `entity_list`, assign doktok types, and
  validate / direction-correct against `PREDICATE_TYPE_PAIRS` (the single source of truth).

## Install the runtime (opt-in, heavy)

```bash
make ner-models      # uv pip install gliner rapidfuzz
```

First model use downloads weights. NER/NuNER use `GLiNER.predict_entities`; GLiNER-Relex relations
use the relex model's own one-pass `inference(... return_relations=True)` (it loads as a custom
`UniEncoderSpanRelexGLiNER`). No `gliner.multitask` extras (`datasets`/`scikit-learn`/`evaluate`) are
needed. Importing the provider package is light; a model loads only when an extractor is instantiated.

## Use it in the pipeline (opt-in)

```bash
DOKTOK_NER_BACKEND=gliner    # or: nuner            (default: the configured LLM NER)
DOKTOK_REL_BACKEND=gliner-relex                     # (default: the configured LLM relations)
DOKTOK_NER_MODEL=... / DOKTOK_REL_MODEL=...         # optional HF model id overrides
DOKTOK_NER_DEVICE=cuda                              # optional torch device (default cpu)
```

The worker (`apps/worker/.../composition.py`) swaps only the chosen extractor when these are set;
the rest of enrichment is unchanged. The local models need no egress, so they work under no-egress.

## Which backend to use

Two options per capability (full analysis in
[ADR-0023](../../docs/adr/ADR-0023-pluggable-ner-and-relation-backends.md)):

- **NER** — default **remote OpenAI** (`gpt-4o-mini`, most accurate); **local GLiNER**
  (`gliner_large-v2.5`) is the recommended on-host alternative (~9x faster, no egress).
- **Relations** — default **local GLiNER-Relex** (`knowledgator/gliner-relex-large-v1.0`): best F1
  *and* faster *and* local; **remote OpenAI** is the alternative (higher recall, low precision).

NuNER and local Ollama LLMs are **not** offered for NER: NuNER's accuracy is too low (see below) and
Ollama NER (qwen3:14b / qwen3.6:27b) is far too slow to be practical (minutes per document).

## Benchmark vs the current LLM

```bash
make ner-bench       # NER:       current LLM vs gliner vs nuner   (eval/golden_entities.json)
make kg-bench        # relations: current LLM vs gliner-relex      (eval/golden_edges.json)
```

`kg-bench` grounds both backends on the same gold entities so it measures relation quality in
isolation. Both report P/R/F1 (per-type / per-predicate), counts, and latency; backends whose
runtime or config is unavailable are skipped, not fatal.

NER benchmark (7 docs; OpenAI `gpt-4o-mini` as the live pipeline; GLiNER/NuNER on CPU):

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

GLiNER reaches 81% relaxed F1 at ~9x the speed of the OpenAI LLM, fully local. NuNER is not viable
(0.0 on PERSON, noisy). Full analysis in ADR-0023.

Relation benchmark (7 docs, 3 gold edges; both grounded on the same gold entities):

```
backend                       P      R     F1  edges   ms/doc
-------------------------------------------------------------
current (OpenAI gpt-4o-mini)  27.3  100.0  42.9     11   1770.3
gliner-relex (relex-large-v1) 50.0   66.7  57.1      4   1163.2  *
```

GLiNER-Relex wins F1 (57.1 vs 42.9), is faster, and is local — so it is the **default for
relations**. The LLM has 100% recall but floods spurious edges (every merchant as `CUSTOMER_OF` →
American Express), dropping precision to 27%. Full analysis in ADR-0023.
