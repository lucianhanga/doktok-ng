# ADR-0003: Ollama as the Default Local Model Runtime

## Status

Proposed

## Context

DokTok NG should be local-first and avoid remote AI providers by default. The system needs chat model
access, embedding model access, and later vision/OCR model access.

## Decision

DokTok NG will use Ollama as the default local model runtime.

Defaults (both configurable via environment variables):

```env
DOKTOK_DEFAULT_MODEL=qwen3.6:35b-a3b
DOKTOK_EMBEDDING_MODEL=qwen3-embedding:0.6b
DOKTOK_OLLAMA_BASE_URL=http://localhost:11434
```

Notes on model selection:

- `qwen3.6:35b-a3b` is the default chat/RAG model. It is a mixture-of-experts model that activates a
  small number of parameters per token, giving strong reasoning quality at practical local speed, and
  it follows structured-output/JSON-extraction instructions well for grounded RAG with citations.
- `qwen3-embedding:0.6b` (1024-dim) is the default embedding model: unlike `mxbai-embed-large` (which
  truncates inputs at 512 tokens) it handles DokTok's larger chunks, and it is strong + multilingual.
  Documented alternatives (all 1024-dim, so no schema change): `mxbai-embed-large:latest`,
  `bge-m3:latest` (~8K context, dense+sparse+ColBERT). The embedding model is configurable. **Changing
  the embedding model requires re-embedding the corpus** - bump `ChunkEmbedFeature`'s version and the
  feature reconciler (ADR-0009) re-indexes every active document; the pgvector column stays
  `vector(1024)` only if the new model is also 1024-dim.
- A lighter chat fallback for ~16GB machines is `qwen3:14b`.
- OCR (M3) uses a local **vision** model rather than bundling Tesseract, configurable via
  `DOKTOK_OCR_MODEL` (default `glm-ocr:latest` - a small dedicated OCR model that emits
  Markdown/JSON/LaTeX). Alternatives: `qwen3-vl:8b`, `qwen2.5vl:7b`. A traditional OCR engine
  (OCRmyPDF/Tesseract) may be kept later as a precision fallback for tables/auditable accuracy.
- For scanned PDF pages that already carry an embedded text layer, the default chat model
  (`DOKTOK_DEFAULT_MODEL`) acts as an **LLM judge** deciding whether the embedded text or the fresh
  OCR is better, so a good existing text layer is not destroyed by weaker OCR. A `text_quality`
  heuristic is the fast-path/fallback (`DOKTOK_OCR_MIN_TEXT_QUALITY`).

Remote providers may be added later behind an adapter, but must be disabled by default.

## Consequences

Positive:

- local-first; aligns with PersonalAI
- simple developer setup
- avoids remote data exposure by default

Negative:

- user must have sufficient local hardware
- model availability depends on the local Ollama installation
- performance varies by machine
