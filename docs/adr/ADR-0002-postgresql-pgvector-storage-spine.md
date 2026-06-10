# ADR-0002: PostgreSQL + pgvector as the First Storage Spine

## Status

Proposed

## Context

DokTok NG needs relational metadata, ingestion jobs, audit events, full-text search, entity search,
and vector search. Adding multiple databases early would make the system harder to operate.

## Decision

DokTok NG will use PostgreSQL as the first storage spine. It will use:

- relational tables for metadata
- JSONB for flexible extraction artifacts
- PostgreSQL full-text search for exact and lexical retrieval
- pgvector for semantic retrieval
- normalized tables for entities
- audit tables for sensitive events

## Consequences

Positive:

- one database to operate
- strong transactional guarantees
- good local-first story
- supports hybrid search without extra infrastructure
- easy backups

Negative:

- not as specialized as a dedicated vector database
- may require tuning as document volume grows

Qdrant, Elasticsearch, or a graph database may be added later behind adapters only if PostgreSQL
proves insufficient.
