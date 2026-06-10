# ADR-0004: Folder-Based Ingestion with Database Job State

## Status

Proposed

## Context

The desired workflow is simple: the user drops files into an ingest folder and DokTok NG processes
them. Folders alone are not enough to track retries, status, failures, deduplication, and
auditability.

## Decision

DokTok NG will use folder-based ingestion plus database-backed job state.

Folder lifecycle:

```
storage/files/ingest
storage/files/in.process
storage/files/docs.active
storage/files/docs.failed
storage/files/quarantine
```

Database job lifecycle:

```
queued -> detecting -> hashing -> normalizing -> extracting -> chunking ->
embedding -> indexing -> activating -> active | failed | quarantined
```

## Consequences

Positive:

- simple user workflow
- reliable processing state, retry support, good observability, failure handling

Negative:

- requires careful coordination between filesystem and database state
- needs idempotent processing

## Implementation notes

- Wait until files are stable (size and mtime unchanged for a configurable interval) before processing.
- Use atomic moves.
- Compute SHA-256 for deduplication.
- Never mark a document `active` before indexing succeeds.
