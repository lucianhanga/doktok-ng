# ADR-0014: Runtime AI model selection (per-purpose, with opt-in OpenAI)

## Status

Accepted

**Revised (2026-07-20, epic #708): the model stack is per-tenant.** Resolution per purpose is
**tenant override → console-global saved settings → env defaults**; the five LLM purposes
(pipeline / RAG / NER / KEG / rerank) and `no_egress` are tenant-overridable, while embedding and
OCR stay deployment-global. Settings are resolved per tenant at request/ingest time — no restart.
The original "single-user system configuration, applies on restart" bullets below are superseded
and kept only where they still describe the console-global layer.

## Context

Model choice was fixed by environment variables (`DOKTOK_DEFAULT_MODEL`, `DOKTOK_ENRICH_MODEL`, ...),
changeable only by editing `.env` and restarting. Two needs pushed past that:

- **Different jobs want different models.** Cheap, dense, fast extraction suits the ingestion pipeline
  (feature extraction on every document), while RAG / interrogation wants the higher-quality model.
  These are independent choices, not one global model.
- **Sometimes a remote model is worth it** — a hard interrogation, or a cheap remote model for an easy
  bulk job — even though the project is local-first and no-egress *by default* (ADR-0006). That has to
  be an explicit, visible, opt-in exception, not a silent capability.

We also wanted this configurable by the operator at runtime (a Settings tab), not only by editing the
environment, while keeping the env defaults as the safe baseline.

## Decision

Add **per-purpose AI model selection** as persisted settings, resolved per tenant at request time,
with OpenAI as an opt-in remote provider behind the same port as Ollama.

- **Per purpose, not global.** Five selectable LLM purposes: the **pipeline** (feature extraction),
  **RAG / interrogation**, **NER**, **KEG** (relation extraction) and **rerank**. Each picks
  `(provider, model, context size)` plus a reasoning density. **Embedding and OCR are
  deployment-global**: the embedding model fixes the index vector dimension (a per-tenant change
  would silently split the index), and OCR sizes the host worker pool.
- **A unified reasoning-density control** (`off|low|medium|high`) that each provider maps to its own
  knob (Ollama `think` on/off; OpenAI `reasoning_effort`), so the UI exposes one concept regardless of
  backend.
- **A catalog as the single source of truth** for what is selectable (`core/.../settings/catalog.py`),
  served to the UI; selections are validated against it.
- **Three-layer resolution, per tenant (epic #708).** Each purpose resolves
  **tenant override → console-global saved settings → env defaults**:
  - The **tenant override** is a per-purpose partial stored in `tenant_ai_settings`
    (migration `0056`), written by the tenant admin in the UI (Settings → Model stack →
    "Your tenant override"); unset purposes fall through. The tenant's `no_egress` is tri-state
    (override → console global → env default **on**), and the host `DOKTOK_NO_EGRESS_LOCK` forces
    no-egress on for every tenant as a floor the UI cannot lower.
  - The **console-global saved settings** live in the `app_settings` key→JSON table
    (migration `0017`), written by the host console (scripts / the static host token), merged over
    the env defaults.
  - The **env defaults** (`DOKTOK_DEFAULT_MODEL`, ...) remain the baseline when nothing is set.
- **Resolved live, not on restart.** The backend resolves the effective stack per request and the
  worker re-resolves on a short interval (`ai_reload` clears its memoized per-tenant clients), so a
  tenant override takes effect without a restart. Writes are validated against the tenant's
  *resulting* egress posture (a selection that would send content off-host under no-egress is
  rejected 422), and the sinks re-check at runtime.
- **OpenAI is opt-in and off by default.** Selecting an OpenAI model is the explicit exception to
  local-first / no-egress (ADR-0006); the defaults are Ollama-only. The OpenAI adapters live in
  `providers/openai` behind the existing chat/extraction ports.
- **The OpenAI API key is write-only.** It can be set or cleared through the API but is never returned;
  `GET` reports only whether a key is configured. (`AiSettingsUpdate.openai_api_key`: `None` leaves it
  unchanged, `""` clears it, a value sets it.)

API: `GET /api/v1/settings/ai/catalog`, `GET /api/v1/settings/ai` (tenant-effective + the console
defaults + the tenant's own override layer), `PUT /api/v1/settings/ai` (console-global, host token
only), `PUT`/`DELETE /api/v1/settings/ai/override` (tenant admin).

## Consequences

Positive: each tenant tunes cost/quality per job without editing `.env` and without a restart;
remote inference stays an explicit, visible opt-in per tenant; the write-only key keeps the secret
out of every response; the provider abstraction means new providers slot in behind the same port;
the host keeps a floor (env defaults + the no-egress lock) that no tenant can lower.

Negative: three configuration layers now exist (tenant override → console global → env), with the
resolution order as the contract every sink must use; enabling OpenAI is a genuine egress decision
each tenant admin owns for their tenant; and the catalog must be kept in step with what the
providers actually support.

## Alternatives considered

- **Env-only configuration (status quo).** Simple and fully local, but no per-purpose split and no
  runtime change without editing files and restarting by hand. Kept as the default baseline, extended
  rather than replaced.
- **Deployment-global settings only (the original decision).** One stack for everyone, changed by
  the host console. Rejected in epic #708: the data-egress posture and model choices are the
  *tenant's* decision (their documents, their compliance posture), so the override is per tenant
  while the host keeps the defaults and the no-egress floor.
- **Per-tenant embedding/OCR overrides.** Rejected: the embedding model fixes the index vector
  dimension (mixing dimensions in one index breaks retrieval), and OCR concurrency sizes the host
  worker pool — both are deployment-global by nature.
- **Live hot-reload of model settings.** Originally avoided in favour of restart-to-apply; made
  unnecessary by per-tenant resolution — the worker re-resolves on a short interval and the backend
  resolves per request, so changes apply live without a restart.
- **A separate remote-provider feature flag instead of per-purpose selection.** Coarser; folding the
  provider into the same per-purpose catalog keeps one mental model and one validation path.

## Related files

- `apps/backend/doktok_api/routers/settings.py` — the settings endpoints (write-only key handling,
  the tenant override write/reset).
- `core/doktok_core/settings/catalog.py` — `MODEL_CATALOG`, reasoning levels.
- `core/doktok_core/settings/effective.py` — the per-tenant three-layer resolution.
- `apps/worker/doktok_worker/composition.py` — per-tenant client resolution + `ai_reload`.
- `contracts/doktok_contracts/schemas.py` — `AiSettings`, `AiSettingsResponse`, `AiSettingsUpdate`,
  `TenantAiSettings`, `ModelOption`, `ModelCatalog`.
- `contracts/doktok_contracts/ports.py` — `AppSettingsRepository` (incl. `*_tenant_ai_settings`).
- `storage/postgres/migrations/0017_app_settings.sql`,
  `storage/postgres/migrations/0056_tenant_ai_settings.sql`.
- `providers/openai/` — OpenAI chat/extraction adapters.
- `apps/ui/src/SettingsPanel.tsx` — the Settings → Model stack UI (defaults card + tenant override
  card).

## Date

2026-06-12 (revised 2026-07-20, epic #708)
