# ADR-0020: Hybrid deployment topology for limited production (local OCR + embeddings, remote OpenAI enrichment + chat)

## Status

Accepted

## Context

DokTok NG must be deployable to small, cheap hardware for a limited production run. The target box is
a TRIGKEY N95: an Intel N95 (4 cores), 8 GB RAM, no discrete GPU. The bundled OS is Windows, though
Linux is preferred for a server role.

The local-first defaults (ADR-0003, ADR-0006) assume a much larger machine. The default models do not
fit on 8 GB:

- chat / RAG: `qwen3.6:35b-a3b` (~23 GB MoE),
- enrichment / OCR-quality judge: follow the Data Pipeline model selected in the UI (no separate
  model).

The performance budget (`docs/operations/performance-and-ollama.md`) shows the full local stack needs
roughly 48-64 GB of unified memory (Apple Silicon) or a 64 GB box with a GPU. That is infeasible on an
N95.

Two facts in the code make a partial split possible:

- **Provider selection is per-purpose at runtime** (ADR-0014). `AiSettings.pipeline` (feature
  extraction) and `AiSettings.rag` (interrogation / chat + rerank) each carry `(provider, model,
  context, reasoning)`, stored in the `app_settings` table and applied on the next backend/worker
  restart. Either purpose can independently be `ollama` (local) or `openai` (remote).
- **OCR and embeddings are hardwired local**, not part of the per-purpose selection. The worker wires
  `OllamaEmbeddingProvider` and `PaddleOcr` unconditionally in
  `apps/worker/doktok_worker/composition.py`; the backend's retriever wires `OllamaEmbeddingProvider`
  unconditionally in `apps/backend/doktok_api/dependencies.py` (`get_retriever`). There is no remote
  embedding or remote OCR adapter today.

So the heavy LLM work (enrichment, chat, rerank) can move to a remote API while OCR and embeddings
stay local on the small box.

## Decision

Deploy DokTok NG in a **hybrid split** for the limited-production target:

- **Local on the N95:** OCR (PaddleOCR, CPU-only) and embeddings (Ollama
  `qwen3-embedding:0.6b`, 1024-dim) and the Postgres + pgvector spine and Gotenberg office conversion.
- **Remote on OpenAI:** the enrichment pipeline (`AiSettings.pipeline` -> `provider: openai`) and RAG
  chat + listwise rerank (`AiSettings.rag` -> `provider: openai`), set through the Settings UI with an
  OpenAI API key.
- **Two environments:** a staging environment for validation and a separate limited-production
  environment, each on its own N95-class box with its own database and settings.

### Why it works in the code

- **Per-purpose runtime selection (ADR-0014).** Setting `pipeline.provider = openai` makes the worker
  build the OpenAI enrichment adapters (`OpenAiMetadataExtractor`, `OpenAiCategoryClassifier`,
  `OpenAiRecordExtractor`, `OpenAiEntityNerExtractor`) instead of the Ollama ones; setting
  `rag.provider = openai` makes the backend build `OpenAiChatModelProvider` for the answerer and
  reuse it for the reranker. Both fall back to the local default model if the key is missing.
- **Embeddings and OCR stay local by construction.** The embedding provider and OCR extractor are not
  behind the per-purpose switch; they are wired to Ollama / PaddleOCR directly in the composition
  roots, so the hybrid split does not change them.
- **No re-index.** Keeping embeddings on `qwen3-embedding:0.6b` preserves the 1024-dim pgvector
  schema. The embedding model is intentionally not selectable (ADR-0014) precisely because changing it
  would change the vector dimension and force a schema migration plus a full re-index. The hybrid
  split therefore needs no re-embedding of existing documents.
- **The small local model fits.** `qwen3-embedding:0.6b` is ~0.7 GB of weights and the embedding
  context is capped at `DOKTOK_EMBEDDING_NUM_CTX` (default 1024); PaddleOCR is CPU-only and loads no
  Ollama model. That is the only Ollama model the box needs to keep resident.

## Consequences

### Positive

- DokTok NG runs on an 8 GB / 4-core box that could never host the local LLMs.
- Document privacy for the index itself is preserved on-box: OCR text, chunks, and embeddings are
  computed and stored locally; pgvector and the file tree never leave the host.
- Cost/quality is tunable per purpose at runtime without redeploying.

### Negative / trade-offs

- **Content egress to OpenAI.** With the pipeline on OpenAI, document text (the enrichment head, and
  for some features more) is sent to OpenAI; with RAG on OpenAI, the retrieved chunks and the user's
  question are sent to OpenAI. This is a deliberate departure from the local-first / no-egress posture
  of **ADR-0006**, taken only for this constrained deployment. ADR-0014 already frames selecting an
  OpenAI model as the explicit, opt-in exception to ADR-0006; this ADR records doing so as the
  standing configuration for the N95 target rather than a one-off.
- **`DOKTOK_NO_EGRESS` does not yet cover OpenAI.** Today the `no_egress` validator
  (`core/doktok_core/config.py`) only checks that `DOKTOK_OLLAMA_BASE_URL` is loopback; it does **not**
  block OpenAI calls. For the hybrid split it must be left `false` (otherwise startup is unaffected,
  but the flag's name overstates what it enforces). Reconciling the flag so it actually gates OpenAI
  egress is tracked as **APP-3** in the M11 epic and is **not built today**.
- **The OpenAI API key is persisted in Postgres `app_settings` as plaintext JSON**
  (`PostgresAppSettingsRepository.set_openai_api_key` -> `_set`, no encryption). It is write-only over
  the API (never read back), but a database backup of `app_settings` is **secret-bearing**: treat
  backups as secrets. Encrypting the stored key is tracked as **APP-8** in the M11 epic and is **not
  built today**.
- **Per-document and per-chat-turn cost.** Cost now scales with corpus size (every document is
  enriched remotely) and with chat usage (every turn calls the remote model, plus a rerank call). The
  reconciler fans out wider for the remote pipeline (`DOKTOK_OPENAI_RECONCILE_CONCURRENCY`, default
  10) because remote APIs parallelize well, which also concentrates spend during backfills.
- **OCR remains the local throughput governor.** PaddleOCR is CPU-bound at roughly one core per page
  and ~seconds per page; on a 4-core N95 that, not the remote LLM, bounds ingestion throughput.

## Alternatives considered

- **Full local stack (ADR-0006 default).** Everything on-box, no egress. Needs ~48-64 GB unified
  memory (Apple Silicon) or a 64 GB box with a GPU to hold `qwen3.6:35b-a3b` +
  embeddings (see `docs/operations/performance-and-ollama.md`). Infeasible on an N95. Rejected for
  this target only; it remains the recommended posture wherever the hardware allows.
- **Hybrid with a separate LAN Ollama host.** Point the small box at a beefier on-premises Ollama
  server for enrichment and chat (`DOKTOK_OLLAMA_BASE_URL` over the LAN). Keeps all content on-prem
  (no OpenAI egress) and preserves ADR-0006's intent, but requires owning and operating that second
  machine. Viable where such a host exists; not assumed for the N95 limited-production run. (Note:
  with `DOKTOK_NO_EGRESS=true` the loopback check would reject a non-loopback Ollama URL, so this
  variant runs with `NO_EGRESS=false` and a LAN URL.)
- **Fully remote.** Move embeddings and OCR to remote APIs too. Rejected: there are no remote
  embedding/OCR adapters today, and moving embeddings remote would risk changing the vector dimension
  and force a re-index; keeping them local is both simpler and keeps the index on-box.

## Reference

- Milestone: **M11 — Deployment** (the deployment epic; this ADR is its founding decision). Ticket
  codes referenced above (APP-2, APP-3, APP-8, ...) live in the M11 epic, not duplicated here.
- Operator guide: `docs/operations/deployment-trigkey-n95.md`.

## Related files

- `core/doktok_core/config.py` — `no_egress` validator (loopback-only today), N95 tuning settings.
- `apps/worker/doktok_worker/composition.py` — per-purpose pipeline provider; hardwired local
  embeddings + OCR; `openai_reconcile_concurrency` fan-out.
- `apps/backend/doktok_api/dependencies.py` — per-purpose RAG provider (`_build_rag_chat_model`);
  hardwired local embedding retriever (`get_retriever`).
- `storage/postgres/doktok_storage_postgres/repositories.py` — `PostgresAppSettingsRepository`
  (plaintext key storage).
- `docs/operations/performance-and-ollama.md` — the memory budget that rules out the full local stack.

## Related decisions

- ADR-0003 (Ollama default local model runtime) — the local baseline this deployment narrows.
- ADR-0006 (local-first, no-egress security) — the posture this deployment deliberately relaxes for
  the constrained target; APP-3 (M11) will reconcile the `DOKTOK_NO_EGRESS` flag with that relaxation.
- ADR-0014 (runtime AI model selection) — the per-purpose provider switch that makes the split
  possible.

## Date

2026-06-16
