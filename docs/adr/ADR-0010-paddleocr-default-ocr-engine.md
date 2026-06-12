# ADR-0010: PaddleOCR as the default OCR engine

## Status

Accepted

## Context

OCR (ADR-0003 / M3) was originally a local Ollama vision model (`glm-ocr`). A generative VLM has two
problems on real documents: it **hallucinates** (it once injected repeated CJK characters into German
invoices) and it falls into **repeat-loops** on sparse/stamp pages, filling `content.md` with garbage
that then poisons embeddings, RAG context, and enrichment (titles/summaries). It also competes with
the chat/enrichment models for the GPU memory budget on a single box.

## Decision

Default `DOKTOK_OCR_ENGINE=paddleocr` — PaddleOCR (PP-OCRv5 mobile det+rec) behind the existing
`OcrExtractor` port (`providers/paddleocr`). Its output is kept shape-compatible with the previous
adapter (one `OcrPageResult` with page text in reading order + a confidence), so nothing downstream
changes.

Key properties:

- It is a **detection + recognition** pipeline (DBNet + CTC), not a generative model, so it is
  structurally immune to repeat-loops and hallucination — a sparse page simply yields little/no text.
- It runs **CPU-only** on Apple Silicon (no Metal), so it never evicts the resident LLMs.
- It exposes a **native per-line confidence** that feeds the embedded-vs-OCR quality heuristic.
- The heavy `paddleocr`/`paddlepaddle` runtime is an **optional extra** (lazy-imported); CI and light
  installs stay slim. Install on the worker host with `uv pip install paddleocr paddlepaddle`.

The legacy `glm-ocr` Ollama vision adapter remains selectable (`DOKTOK_OCR_ENGINE=glm-ocr`) and is
hardened (repeat-penalty + runaway-repetition trimming) as a fallback.

## Consequences

- OCR quality and determinism improve markedly; garbage no longer reaches the corpus.
- The worker host gains a Python extra (`paddleocr`, `paddlepaddle`); documented in README/.env.
- OCR is CPU-bound (~1-2 s/page on the mobile models), which is acceptable and frees the GPU for the
  chat/enrichment models. Documents OCR'd by the old engine are cleaned by re-ingesting them.
