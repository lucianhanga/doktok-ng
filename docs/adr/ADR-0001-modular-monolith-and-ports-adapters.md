# ADR-0001: Modular Monolith and Ports/Adapters

## Status

Proposed

## Context

DokTok NG must remain maintainable by one developer plus coding agents. The system needs clear
boundaries for ingestion, extraction, indexing, retrieval, storage, model providers, and MCP access.
A microservice architecture would add unnecessary operational complexity at the start.

## Decision

DokTok NG will be implemented as a modular monolith using ports and adapters.

Core domain logic depends on interfaces (ports), not infrastructure libraries. Adapters implement
infrastructure details such as PostgreSQL, the local filesystem, Ollama, MIME detection, OCR tools,
PDF extraction tools, and MCP transport. `import-linter` enforces the dependency direction so core
never imports adapters. The worker runs as a separate process but shares the same core packages.

## Consequences

Positive:

- simpler local development
- easier testing and refactoring
- clear boundaries for coding agents
- future services can be split out if needed

Negative:

- requires discipline to maintain module boundaries
- not horizontally scalable by default
