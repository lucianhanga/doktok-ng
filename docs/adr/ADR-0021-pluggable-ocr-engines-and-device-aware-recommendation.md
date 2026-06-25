# ADR-0021: Pluggable OCR engines and device-aware recommendation

## Status

Accepted (recommendation shipped; RapidOCR adapter and Settings-UI engine selection planned, M17 #375)

## Context

[ADR-0010](ADR-0010-paddleocr-default-ocr-engine.md) made PaddleOCR (PP-OCRv5 mobile det+rec) the
default OCR engine behind the `OcrExtractor` port, with the legacy `glm-ocr` Ollama vision adapter as
a selectable fallback. That decision held up — OCR quality and determinism improved markedly — but two
gaps emerged once DokTok NG started running on varied hardware (ADR-0020, the TRIGKEY N95):

- **No single engine is best on every host.** PaddleOCR's PaddlePaddle backend is heavy on CPU and its
  oneDNN kernels **crash on Intel N95 / Alder Lake-N** under the PIR executor
  (`Unimplemented ... onednn_instruction.cc`); see the oneDNN caveat in ADR-0010. A GPU box, an Intel
  CPU box, and a generic CPU box each have a different best-fit engine and parallelism.
- **Operators had no guidance.** Choosing `DOKTOK_OCR_ENGINE` and `DOKTOK_OCR_CONCURRENCY` was manual
  and easy to get wrong (too-high concurrency OOM-kills PaddleOCR worker processes — `BrokenProcessPool`).

The `OcrExtractor` port already makes the engine swappable in principle. This ADR records the decision
to make that pluggability first-class and to add a host-aware recommendation that guides operators
toward the right engine and concurrency.

## Decision

Treat OCR as a **pluggable, device-aware** capability behind the existing `OcrExtractor` port.

**Shipped now (M17, #375): device-aware recommendation.**

- `GET /api/v1/settings/ocr/recommendation` (auth-gated) probes the host and returns
  `{engine, concurrency, reason}`. The host probe (`apps/backend/doktok_api/routers/settings.py`
  `_probe_hardware`) reads CPU vendor (`/proc/cpuinfo`), logical core count, total RAM
  (`/proc/meminfo`), and NVIDIA GPU presence (`nvidia-smi` / `/proc/driver/nvidia/version`); it is
  best-effort and never raises.
- The pure recommendation logic lives in `core/doktok_core/settings/ocr_recommend.py`
  (`recommend_ocr`). Parallelism is bounded by cores (~1 core per OCR process, minus one for the OS)
  **and** RAM (~1.5 GB per process), capped at 6. The current rules:
  - **GPU present** → `rapidocr`, concurrency `min(cores, 8)` — run OCR on the GPU provider.
  - **Intel CPU** → `rapidocr` with the OpenVINO backend (fastest same-quality option on Intel).
  - **Other CPU** → `rapidocr` (ONNX) — lighter and faster than PaddlePaddle at the same quality.
- The Settings UI shows the recommendation as a hint with one-click apply for the suggested
  concurrency. It is **advisory** — it does not change the running engine on its own.

**Planned (M17, not yet in the repo):**

- **Live OCR engine selection in the Settings UI** (no restart), persisted like the other OCR
  settings.
- **A RapidOCR adapter** (ONNX / OpenVINO): the same PP-OCR det+rec models exported to ONNX, run via
  ONNXRuntime (OpenVINO execution provider, optional INT8 on Intel). Expected to match PaddleOCR's
  quality while being lighter and faster on CPU, and — importantly — it **avoids the PaddlePaddle
  oneDNN crash** on N95-class Intel CPUs. It must be **benchmarked against PaddleOCR on the N95 before
  it becomes the default**.

Until the RapidOCR adapter lands and is benchmarked, **PaddleOCR remains the default** (ADR-0010), and
the recommendation's `rapidocr` suggestion is an informational target, not a selectable engine yet.

## Consequences

- Operators get host-appropriate engine + concurrency guidance instead of guessing, reducing both
  the oneDNN crash (PaddleOCR on N95) and the OOM/`BrokenProcessPool` failure mode (too-high
  concurrency).
- The recommendation can name an engine (`rapidocr`) that is **not yet selectable**. This is
  intentional and documented as planned; the UI presents it as a target, and `DOKTOK_OCR_ENGINE`
  still accepts only `paddleocr` (default) and `glm-ocr` today.
- Adding RapidOCR is a new adapter behind the same port, so downstream (routing, searchable PDF,
  confidence heuristic) is unaffected, mirroring how PaddleOCR was introduced.
- The recommendation probe is Linux-oriented (`/proc`); on macOS it degrades gracefully (cores from
  `os.cpu_count()`, vendor/RAM may be empty) and still returns a usable concurrency.

## Alternatives considered

- **Keep PaddleOCR-only and document the N95 workaround** (disable oneDNN). This is the stopgap in
  place today (`DOKTOK_OCR_ENABLE_MKLDNN=false`), but it leaves PaddleOCR slower on Intel and does not
  help operators size concurrency. Kept as the interim default; superseded as the long-term path by
  the RapidOCR adapter.
- **Auto-apply the recommendation** (switch engine/concurrency automatically). Rejected for now:
  engine changes affect throughput and the model cache, so an operator-confirmed hint is safer than a
  silent switch.

## Related files

- `core/doktok_core/settings/ocr_recommend.py` — pure recommendation logic (`recommend_ocr`)
- `apps/backend/doktok_api/routers/settings.py` — host probe + `GET /ocr/recommendation`
- `core/doktok_core/config.py` — `ocr_engine`, `ocr_concurrency`, `ocr_cpu_threads`,
  `ocr_enable_mkldnn`
- `providers/paddleocr/doktok_provider_paddleocr/ocr.py` — current default adapter (process pool)

## Related decisions

- [ADR-0010 — PaddleOCR as the default OCR engine](ADR-0010-paddleocr-default-ocr-engine.md)
- [ADR-0003 — Ollama default local model runtime](ADR-0003-ollama-default-local-model-runtime.md)
- [ADR-0020 — Hybrid deployment topology](ADR-0020-hybrid-deployment-topology.md)
- [ADR-0014 — Runtime AI model selection](ADR-0014-runtime-ai-model-selection.md)

## Date

2026-06-25.
