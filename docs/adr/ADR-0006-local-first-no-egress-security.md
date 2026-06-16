# ADR-0006: Local-First and No-Egress-by-Default Security Posture

## Status

Proposed

## Context

DokTok NG processes private documents. It must be safe by default. Risks include malicious files,
prompt injection inside documents, accidental remote model calls, MCP overexposure, unsafe file
handling, and leaking document contents to external systems.

## Decision

DokTok NG will be local-first and no-egress by default.

Default behavior:

- local filesystem storage, local PostgreSQL, local Ollama
- no remote AI providers
- no external network calls unless explicitly configured (`DOKTOK_NO_EGRESS=true`)
- read-only MCP server first
- audit all sensitive operations

All document content, extracted text, model output, tool output, and MCP input must be treated as
untrusted.

## Consequences

Positive:

- privacy-preserving default
- safer local deployment
- clearer trust model, suitable for sensitive documents

Negative:

- fewer cloud conveniences by default
- user must explicitly configure integrations

## Required controls

- MIME allowlist
- file size limits
- page count limits
- quarantine folder
- no execution of document content
- audit log
- explicit permission for future write tools
- explicit configuration for any remote provider

## Remote AI providers and the no-egress gate (M11, APP-3)

`DOKTOK_NO_EGRESS` was originally enforced only against the local model endpoint: startup refuses a
non-loopback `DOKTOK_OLLAMA_BASE_URL`. That check does not cover remote AI providers, so selecting
OpenAI for the enrichment pipeline or RAG (ADR-0014) would send document content off the host even
with no-egress on.

To keep the posture coherent, selecting OpenAI is now gated on egress being permitted:

- The OpenAI provider is used only when an API key is configured **and** `DOKTOK_NO_EGRESS=false`
  (`openai_egress_allowed()`).
- If a purpose is set to OpenAI while `DOKTOK_NO_EGRESS=true`, the system refuses to egress, logs a
  clear warning naming the setting, and falls back to the local default model rather than silently
  sending content to OpenAI.

The hybrid deployment topology (ADR-0020) therefore requires `DOKTOK_NO_EGRESS=false` as the
explicit opt-in to remote enrichment/RAG, and restricts the actual outbound traffic to the OpenAI
endpoint at the host firewall.
