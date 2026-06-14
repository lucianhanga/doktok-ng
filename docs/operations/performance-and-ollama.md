# Performance & Ollama tuning

DokTok ingestion is mostly IO-bound on the local Ollama model server (OCR, embeddings, chat/RAG, and
the OCR-vs-embedded text judge). Throughput depends on two independent knobs: how many documents
DokTok processes at once, and how many requests Ollama serves at once.

## The two knobs

| Setting | Where | Default | Effect |
|---|---|---|---|
| `DOKTOK_INGEST_CONCURRENCY` | DokTok (`.env`) | `4` | How many stable files the worker processes in parallel. |
| `DOKTOK_OLLAMA_TIMEOUT_SECONDS` | DokTok (`.env`) | `600` | HTTP timeout per Ollama call. Generous so queued requests do not fail. |
| `OLLAMA_NUM_PARALLEL` | **Ollama server** | 1* | How many requests Ollama runs concurrently **per model**. |
| `OLLAMA_MAX_LOADED_MODELS` | **Ollama server** | 1-3* | How many distinct models stay resident at once. |

\* Ollama's defaults vary by version and available memory.

### Why both matter

By default Ollama serves **one request at a time per model**. If `DOKTOK_INGEST_CONCURRENCY=4` but
`OLLAMA_NUM_PARALLEL=1`, the four pipelines still send requests concurrently, but Ollama **queues**
them and runs them one by one. Before the timeout was raised this caused jobs to fail with
`internal_error` ("timed out"); now they wait instead, but you get little speedup on model-bound
work (scanned/OCR documents). To make the model calls actually overlap, raise `OLLAMA_NUM_PARALLEL`.

By default DokTok uses **two** Ollama models - chat `DOKTOK_DEFAULT_MODEL` and embeddings
`DOKTOK_EMBEDDING_MODEL` - because the default OCR engine is **PaddleOCR** (`DOKTOK_OCR_ENGINE=paddleocr`),
which runs locally on CPU and loads **no** Ollama model. If you switch to the legacy vision-OCR engine
(`DOKTOK_OCR_ENGINE=ollama`, model `DOKTOK_OCR_MODEL`, e.g. `glm-ocr:latest`) that adds a third Ollama
model. Keep `OLLAMA_MAX_LOADED_MODELS` at least equal to the number of Ollama models in use (2 by
default, 3 with Ollama OCR) so the pipeline does not unload/reload a model every time it switches steps.

Rule of thumb: set `OLLAMA_NUM_PARALLEL` to roughly `DOKTOK_INGEST_CONCURRENCY`, bounded by memory.

## Configuring the Ollama server

These are environment variables on the `ollama serve` process, not DokTok settings.

**macOS (menu-bar app):**

```bash
launchctl setenv OLLAMA_NUM_PARALLEL 4
launchctl setenv OLLAMA_MAX_LOADED_MODELS 3
# then fully quit and reopen the Ollama app
```

or run the server yourself (quit the app first):

```bash
OLLAMA_NUM_PARALLEL=4 OLLAMA_MAX_LOADED_MODELS=3 ollama serve
```

**Linux (systemd):**

```bash
sudo systemctl edit ollama.service
# add under [Service]:
#   Environment="OLLAMA_NUM_PARALLEL=4"
#   Environment="OLLAMA_MAX_LOADED_MODELS=3"
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

Verify with `ollama ps` (shows resident models) while ingesting; the server startup log prints the
parallelism settings.

## Memory cost of parallelism

The key fact: **model weights are loaded once and shared across all parallel requests. Only the
per-request KV cache (the attention context) scales with `OLLAMA_NUM_PARALLEL`.**

Approximate total memory:

```
total = sum(weights of loaded models)
      + sum(per-model KV-cache-per-slot * OLLAMA_NUM_PARALLEL)
      + overhead (compute/vision buffers, ~1-2 GB)
```

KV cache per slot, per model:

```
kv_per_slot = 2 (K and V) * n_layers * n_kv_heads * head_dim * num_ctx * bytes_per_element
```

`num_ctx` is the context window Ollama allocates per slot (default ~4096 tokens), and
`bytes_per_element` is 2 for an F16 KV cache. **KV scales linearly with both `num_ctx` and
`OLLAMA_NUM_PARALLEL`** - a large context window is far more expensive than parallelism itself.

### Worked example (this repo's default models)

Weights (resident, independent of parallelism):

| Model | Params | Quant | Weights |
|---|---|---|---|
| `qwen3.6:35b-a3b` (chat/RAG) | 36B MoE (3B active) | Q4_K_M | ~23 GB |
| `qwen3-embedding:0.6b` (embeddings) | 0.6B | F16 | ~0.7 GB |
| **Both loaded (default)** | | | **~24 GB** |
| `glm-ocr:latest` (OCR, vision) - only with `DOKTOK_OCR_ENGINE=ollama` | 1.1B | F16 | ~2.2 GB |

The default OCR engine is PaddleOCR (CPU), which loads no Ollama model; the OCR row applies only if
you switch to the Ollama vision-OCR engine.

KV cache added by parallelism, at the default ~4096-token context:

- Chat model: roughly ~0.3-0.5 GB per parallel slot (estimated from typical Qwen3-MoE attention
  dimensions; grows proportionally if you raise the context window).
- OCR model: smaller (~0.1 GB/slot) plus a vision buffer per concurrent image.
- Embedding model: negligible (512-token context).

So, with all three models resident:

| `OLLAMA_NUM_PARALLEL` | Weights | + KV cache | + overhead | **Total (approx)** |
|---|---|---|---|---|
| 1 | ~26 GB | ~0.5 GB | ~1.5 GB | **~28 GB** |
| 2 | ~26 GB | ~1 GB | ~1.5 GB | **~29 GB** |
| 4 | ~26 GB | ~2 GB | ~2 GB | **~30 GB** |

Takeaways:

- The dominant cost is **loading the three models (~26 GB)**, not the parallelism.
- Each extra parallel slot is cheap at the default context (~0.3-0.5 GB), so going from 1 to 4
  parallel adds only ~1.5-2 GB - **provided you keep the context window modest**.
- If you raise the per-request context window, multiply the KV figures accordingly (e.g. a 4x larger
  context makes KV ~4x bigger per slot).
- On Apple Silicon this is **unified memory**: it counts against system RAM. ~26 GB of weights needs
  a 32 GB machine just for the models (tight with the OS); 64 GB+ is comfortable for
  `OLLAMA_NUM_PARALLEL` of 2-4. If memory is tight, drop to a lighter chat model
  (`DOKTOK_DEFAULT_MODEL=qwen3:14b`) or lower `OLLAMA_MAX_LOADED_MODELS` (accepting reload thrash).

## Apple Silicon GPU memory budget (the ~75% "wired limit")

On Apple Silicon the CPU and GPU share **one unified memory pool** - there is no separate VRAM. To
keep the GPU from starving macOS and your other apps, the OS caps how much memory the GPU may "wire
down" for itself. That cap (Metal's `recommendedMaxWorkingSetSize`) defaults to roughly **70-75% of
total RAM**:

```
48 GB machine  x ~0.75  =>  ~36 GB usable by Ollama for weights + KV caches
```

The remaining ~12 GB is reserved for the OS, CPU, and apps. **This is why models evict each other:**
if the resident models plus the one being loaded would exceed ~36 GB, Ollama unloads the
least-recently-used model to make room (also bounded by `OLLAMA_MAX_LOADED_MODELS`). `keep_alive` does
**not** override this - it only prevents *idle* unloading, not eviction under memory pressure.

Concrete example on 48 GB: the chat/RAG model `qwen3.6:35b-a3b` (~23 GB) and the enrichment/judge model
`qwen3:14b` (~12 GB resident) total ~35 GB - right at the ceiling. They cannot both stay resident
alongside OCR + embeddings, so loading one evicts the other and the next call pays a ~14-50 s reload.

### How DokTok avoids this

The whole **ingestion path runs on one dense model** so it stays well under the budget:

| Ingestion role | Model | ~Resident |
|---|---|---|
| OCR (default) | PaddleOCR (CPU, local) | ~0 GB GPU/Ollama |
| OCR (`DOKTOK_OCR_ENGINE=ollama`) | `glm-ocr` (`DOKTOK_OCR_NUM_CTX=8192`) | ~3 GB |
| OCR-quality judge | `qwen3:14b` (`DOKTOK_JUDGE_MODEL`) | shared with enrichment |
| Enrichment (metadata + classify) | `qwen3:14b` (`DOKTOK_ENRICH_MODEL`, 4k ctx) | ~12 GB |
| Embeddings | `qwen3-embedding:0.6b` | ~0.7 GB |
| **Ingestion total** | | **~16 GB** |

The 23 GB `qwen3.6` is only loaded by **RAG chat** (`DOKTOK_DEFAULT_MODEL`, backend-only). So normal
ingestion never evicts the enrichment model. Chatting *during* a large ingest can still load qwen3.6
and compete - avoid that, or raise the limit (below).

### Raising the limit (optional)

The cap is a soft sysctl you can raise to fit both big models (e.g. 40 GB, leaving ~8 GB for the OS):

```bash
sudo sysctl iogpu.wired_limit_mb=40960   # gives the GPU ~40 GB on a 48 GB machine
```

With ~40 GB, `qwen3.6` (23) + `qwen3:14b` (12) = 35 GB both fit, so chat and ingest coexist without
reloads. Caveats: **do not set it near total RAM** (the OS will swap hard or hang), and it **resets on
reboot** (re-run it or add a `launchd` startup job to persist).

## If you are memory constrained

- Lower `DOKTOK_INGEST_CONCURRENCY` and `OLLAMA_NUM_PARALLEL` to 1-2.
- Keep ingestion on the dense models (`DOKTOK_ENRICH_MODEL` / `DOKTOK_JUDGE_MODEL` = `qwen3:14b`) so it
  fits in ~16 GB and never needs the 23 GB qwen3.6.
- Keep `DOKTOK_OLLAMA_TIMEOUT_SECONDS` generous so queued requests finish rather than fail.

## Insights embedding map (M7.1)

The Insights tab fits 2D/3D projections of a tenant's chunk embeddings (ADR-0016). This runs as a
separate worker stream, triggered on demand by the recompute button (it is never automatic).

- **Runtime deps:** `make projection-engine` installs `umap-learn` + `scikit-learn` + `numpy`. These
  are an optional extra and are **not** in the lockfile, so `uv sync` removes them — re-run
  `make projection-engine` (and `make ocr-paddle`) on the worker host after any sync.
- **Cost:** UMAP is CPU-bound and scales with chunk count; it runs off the ingestion/reconcile
  threads so it never stalls them. `DOKTOK_PROJECTION_MAX_POINTS` (default 20000) caps points per
  projection (larger tenants are truncated and flagged in the UI). `DOKTOK_PROJECTION_ALGORITHM`
  (`umap`|`pca`) and `DOKTOK_PROJECTION_VERSION` select the reducer and invalidate the cache.
- **Triggering by hand:** `POST /api/v1/visualizations/embeddings/recompute` enqueues a recompute;
  `GET .../status` reports progress; `GET .../embeddings?dim=2|3` reads the cached map.
