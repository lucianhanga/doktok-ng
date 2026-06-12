# ADR-0012: Referential integrity via FK cascade + activation ordering

## Status

Accepted

## Context

A document's derived rows (chunks, entities, features, category links, extracted records) were only
cleaned up by application code on delete/reingest, which is easy to get wrong and leaves orphans when
a path is missed. We want deleting a `documents` row to reliably remove everything derived from it.

## Decision

Add `ON DELETE CASCADE` foreign keys from `document_chunks`, `document_entities`, `document_features`,
`document_category_links`, and `extracted_records` to `documents(id)`. Deleting a document now removes
all derived rows in one operation, and delete/reingest rely on the cascade instead of bespoke cleanup.

This forces an ordering invariant in the ingestion pipeline: the **`documents` row must be created
before** its chunks/entities are indexed (the children carry the FK). The activation pipeline writes
the canonical artifacts and inserts the `documents` row first, then indexes; if indexing fails, it
deletes the document again so the cascade clears any partial children before the job is failed.

A companion partial unique index (`documents(tenant_id, sha256) WHERE status='active'`) makes "one
active document per content hash per tenant" a database invariant, backstopping the pipeline's
read-then-write deduplication against concurrency.

## Consequences

- Deletes and reingests are clean and complete by construction; no orphan derived rows.
- The pipeline creates the parent row before children (a document is still not *exposed* as active
  until indexing succeeds), and a mid-indexing crash self-cleans via the cascade.
- Content-hash dedup is enforced, not merely checked.
