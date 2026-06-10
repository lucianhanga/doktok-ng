# Security Policy

DokTok NG processes private documents and is designed to be **safe by default**.

## Default posture

- Local-first: local filesystem storage, local PostgreSQL, local Ollama.
- No remote AI providers by default.
- No external network egress unless explicitly configured (`DOKTOK_NO_EGRESS=true`).
- The MCP server is read-only first; write tools require explicit, later permissions.
- All sensitive operations are audited.

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
