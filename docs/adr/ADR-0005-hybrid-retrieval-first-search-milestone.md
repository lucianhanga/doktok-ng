# ADR-0005: Hybrid Retrieval from the First Search Milestone

## Status

Proposed

## Context

Document management requires both semantic search and exact search. Users search for concepts, names,
invoice numbers, contract identifiers, dates, filenames, organizations, places, and exact phrases.
Vector search alone is not enough.

## Decision

DokTok NG will implement hybrid retrieval from the first search milestone (M4). The retrieval stack
combines:

1. pgvector semantic search
2. PostgreSQL full-text search
3. entity/token search

Later improvements may include reranking and query expansion. Vector-only retrieval will not be the
main search path.

## Consequences

Positive:

- better search quality for both exact terms and semantic questions
- stays inside PostgreSQL
- supports RAG citations

Negative:

- scoring and ranking are more complex
- requires tuning

## Implementation notes

Search results should return: document id, chunk id, page number (if available), title/filename,
snippet, score components, and extraction method.
