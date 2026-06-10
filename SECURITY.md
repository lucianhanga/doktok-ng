# Security Policy

DokTok NG processes private documents and is designed to be **safe by default**.

## Default posture

- Local-first: local filesystem storage, local PostgreSQL, local Ollama.
- No remote AI providers by default.
- No external network egress unless explicitly configured (`DOKTOK_NO_EGRESS=true`).
- The MCP server is read-only first; write tools require explicit, later permissions.
- All sensitive operations are audited.

## Authentication and multi-tenancy

- The API requires a bearer token (`Authorization: Bearer <token>`); `/health` is public (ADR-0008).
- Tokens map to a tenant; tenant identity is taken only from the authenticated token, never from
  request input.
- Token comparison is constant-time; tokens are never logged. The backend fails closed when no tokens
  are configured and binds to loopback by default (refuses a non-loopback bind without tokens).
- Data is isolated per tenant via a `tenant_id` on every tenant-owned table and per-tenant filesystem
  folders (ADR-0007). Repositories never expose an unscoped read.
- Static `.env` tokens now; DB-backed hashed/revocable tokens later.

## Untrusted inputs

The following are always treated as untrusted:

- uploaded/dropped files and filenames
- extracted text and OCR output
- PDF metadata and document chunks
- model output and tool output
- MCP input, MCP clients, and MCP servers

## Required controls

- MIME allowlist (file type detected by content, not extension)
- maximum file size and maximum page count
- quarantine folder for suspicious or disallowed files
- no execution of document content
- audit log for sensitive events
- safe prompt construction for RAG, with source citation and provenance

## Reporting a vulnerability

This is a private, single-maintainer project. Report security concerns directly to the maintainer
(lucianhanga). Do not open public issues for sensitive vulnerabilities.
