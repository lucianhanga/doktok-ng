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
hardened (repeat-penalty + runaway-repetition trimming) as a fallback. (`DOKTOK_OCR_ENGINE` selects
PaddleOCR only on the exact value `paddleocr`; any other value routes to the Ollama vision adapter.)

### CPU acceleration caveat (oneDNN / MKL-DNN)

PaddleOCR's PaddlePaddle backend uses oneDNN (MKL-DNN) CPU kernels by default for speed. This is
controlled by `DOKTOK_OCR_ENABLE_MKLDNN` (default `true`). It **must be set to `false` on Intel N95 /
Alder Lake-N** CPUs: PaddlePaddle's oneDNN kernels abort under the PIR executor there with
`Unimplemented ... onednn_instruction.cc`, crashing every OCR page. With oneDNN disabled, PaddleOCR
runs (slightly slower) and reads pages correctly. Leave it `true` on CPUs where oneDNN works.
Implemented in `core/doktok_core/config.py` (`ocr_enable_mkldnn`) and applied in
`providers/paddleocr/doktok_provider_paddleocr/ocr.py` (passed into the worker-process `PaddleOCR`).

### Memory and the worker process pool

PaddleOCR runs in a `ProcessPoolExecutor` of `DOKTOK_OCR_CONCURRENCY` worker processes (it is
GIL-serialized, so real parallelism comes from processes, not threads). Each PaddleOCR process uses
~1-1.5 GB RAM. In a containerized deployment the **worker container's memory cap** (not host RAM)
bounds safe concurrency: exceeding it OOM-kills a child, surfacing as `BrokenProcessPool` /
"a child process terminated abruptly". Fix by lowering `DOKTOK_OCR_CONCURRENCY` or raising the worker
`memory:` cap. On the N95 the validated config is `DOKTOK_OCR_CONCURRENCY=2` with the worker capped at
~2.5 GB. See [deployment-trigkey-n95.md](../operations/deployment-trigkey-n95.md) and
[performance-and-ollama.md](../operations/performance-and-ollama.md).

## Consequences

- OCR quality and determinism improve markedly; garbage no longer reaches the corpus.
- The worker host gains a Python extra (`paddleocr`, `paddlepaddle`); documented in README/.env.
- OCR is CPU-bound (~1-2 s/page on the mobile models), which is acceptable and frees the GPU for the
  chat/enrichment models. Documents OCR'd by the old engine are cleaned by re-ingesting them.

## Forward note: OCR is becoming pluggable

This ADR makes PaddleOCR the default; it does not freeze the engine. OCR is now moving to a
**pluggable, device-aware** model, decided in
[ADR-0021](ADR-0021-pluggable-ocr-engines-and-device-aware-recommendation.md):

- A **device-aware recommendation** is shipped (M17, #375): `GET /api/v1/settings/ocr/recommendation`
  probes the host (CPU vendor/cores, RAM, GPU) and returns `{engine, concurrency, reason}`; the
  Settings UI shows it as a hint.
- A **RapidOCR (ONNX / OpenVINO) adapter** and **live engine selection in the Settings UI** are
  **planned** (M17). RapidOCR runs the same PP-OCR det+rec models exported to ONNX (same quality,
  lighter/faster on CPU, and it sidesteps the oneDNN crash above). It must be **benchmarked against
  PaddleOCR on the N95 before it can become the default** — PaddleOCR remains the default for now.

## Related decisions

- [ADR-0003 — Ollama default local model runtime](ADR-0003-ollama-default-local-model-runtime.md)
  (the original `glm-ocr` vision OCR this superseded as the default)
- [ADR-0021 — Pluggable OCR engines + device-aware recommendation](ADR-0021-pluggable-ocr-engines-and-device-aware-recommendation.md)
- [ADR-0020 — Hybrid deployment topology](ADR-0020-hybrid-deployment-topology.md) (the N95 box where
  the oneDNN/memory caveats bite)

## Date

2026-06-16. CPU-acceleration (oneDNN/MKL-DNN), memory/pool, and pluggable-engine notes added
2026-06-25.
