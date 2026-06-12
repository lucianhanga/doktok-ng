# ADR-0014: Runtime AI model selection (per-purpose, with opt-in OpenAI)

## Status

Accepted

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

Add **per-purpose AI model selection** as persisted system settings, applied on restart, with OpenAI
as an opt-in remote provider behind the same port as Ollama.

- **Per purpose, not global.** Two selectable purposes: the **pipeline** (feature extraction) and
  **RAG / interrogation**. Each picks `(provider, model, context size)` plus a reasoning density.
- **A unified reasoning-density control** (`off|low|medium|high`) that each provider maps to its own
  knob (Ollama `think` on/off; OpenAI `reasoning_effort`), so the UI exposes one concept regardless of
  backend.
- **A catalog as the single source of truth** for what is selectable (`core/.../settings/catalog.py`),
  served to the UI; selections are validated against it.
- **Global system settings, not tenant-scoped.** Stored in an `app_settings` key→JSON table
  (migration `0017`) and **merged over the env defaults at startup**; changes take effect on the next
  backend/worker restart (this is single-user system configuration). The env defaults remain the
  baseline when nothing is set.
- **OpenAI is opt-in and off by default.** Selecting an OpenAI model is the explicit exception to
  local-first / no-egress (ADR-0006); the defaults are Ollama-only. The OpenAI adapters live in
  `providers/openai` behind the existing chat/extraction ports.
- **The OpenAI API key is write-only.** It can be set or cleared through the API but is never returned;
  `GET` reports only whether a key is configured. (`AiSettingsUpdate.openai_api_key`: `None` leaves it
  unchanged, `""` clears it, a value sets it.)

API: `GET /api/v1/settings/ai/catalog`, `GET /api/v1/settings/ai`, `PUT /api/v1/settings/ai`.

## Consequences

Positive: the operator tunes cost/quality per job without editing `.env`; remote inference is
available when wanted but stays an explicit, visible opt-in; the write-only key keeps the secret out
of every response; the provider abstraction means new providers slot in behind the same port.

Negative: changes apply only on restart (settings are read at startup), so the UI must say so;
two configuration sources now exist (env defaults + persisted overrides), with the merge order as the
contract; enabling OpenAI is a genuine egress decision the operator owns, and the catalog must be kept
in step with what the providers actually support.

## Alternatives considered

- **Env-only configuration (status quo).** Simple and fully local, but no per-purpose split and no
  runtime change without editing files and restarting by hand. Kept as the default baseline, extended
  rather than replaced.
- **Live hot-reload of model settings.** Avoids the restart, but means re-reading and re-wiring
  providers mid-run for marginal benefit in a single-user system; restart-to-apply is simpler and
  predictable.
- **A separate remote-provider feature flag instead of per-purpose selection.** Coarser; folding the
  provider into the same per-purpose catalog keeps one mental model and one validation path.

## Related files

- `apps/backend/doktok_api/routers/settings.py` — the settings endpoints (write-only key handling).
- `core/doktok_core/settings/catalog.py` — `MODEL_CATALOG`, reasoning levels.
- `contracts/doktok_contracts/schemas.py` — `AiSettings`, `AiSettingsResponse`, `AiSettingsUpdate`,
  `ModelOption`, `ModelCatalog`.
- `contracts/doktok_contracts/ports.py` — `AppSettingsRepository`.
- `storage/postgres/migrations/0017_app_settings.sql`.
- `providers/openai/` — OpenAI chat/extraction adapters.
- `apps/ui/src/SettingsPanel.tsx` — the Settings tab UI.

## Date

2026-06-12
