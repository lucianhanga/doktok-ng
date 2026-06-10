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
DOKTOK_EMBEDDING_MODEL=mxbai-embed-large:latest
DOKTOK_OLLAMA_BASE_URL=http://localhost:11434
```

Notes on model selection:

- `qwen3.6:35b-a3b` is the default chat/RAG model. It is a mixture-of-experts model that activates a
  small number of parameters per token, giving strong reasoning quality at practical local speed, and
  it follows structured-output/JSON-extraction instructions well for grounded RAG with citations.
- `mxbai-embed-large:latest` (1024-dim) is the default embedding model. A documented alternative is
  `bge-m3:latest` (1024-dim, ~8K token context, multilingual), which is preferable when document
  chunks are large or multilingual. The embedding model is configurable and can be switched without
  changing core code.
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
