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
