# ADR-0007: Multi-Tenancy via Shared Database with tenant_id

## Status

Accepted

## Context

DokTok NG must serve multiple tenants (organizations/users) from one deployment, keeping each tenant's
documents, jobs, search results, entities, and audit events isolated. This is a foundational
requirement: every feature milestone (ingestion, extraction, search, RAG, MCP) must be tenant-aware.

Isolation options considered: shared database with a `tenant_id` discriminator column, a separate
schema per tenant, or a separate database per tenant. Schema- and database-per-tenant give stronger
physical isolation but add significant migration and operational complexity, which conflicts with the
local-first, single-developer-plus-agents goal (ADR-0001).

## Decision

DokTok NG uses a **single shared PostgreSQL database with a `tenant_id` discriminator** on every
tenant-owned table. All reads and writes are scoped to the caller's tenant.

- Every domain table carries a non-null `tenant_id` (e.g. `ingestion_jobs`, and later `documents`,
  `document_versions`, `document_pages`, `document_chunks`, `document_entities`,
  `document_artifacts`, `audit_events`).
- Repository ports take a `tenant_id` for reads (`get`, `list_*`, `find_*`); writes carry it on the
  entity. Deduplication (by SHA-256) is scoped per tenant.
- The filesystem document lifecycle is rooted per tenant:
  `storage/files/{tenant_id}/{ingest,in.process,docs.active,docs.failed,quarantine}`.
- The caller's tenant is established by authentication (ADR-0008), never trusted from request bodies.

This can evolve to schema- or database-per-tenant later behind the same repository ports without
changing core logic.

## Consequences

Positive:

- one database to operate; simplest migrations and backups
- straightforward local-first development
- clear, uniform scoping rule (filter by `tenant_id`) for every query

Negative:

- isolation is logical, not physical: a missing `tenant_id` filter is a cross-tenant leak, so scoping
  must be enforced at the repository boundary and covered by tests
- noisy-neighbour effects are possible at scale (acceptable for now)

## Required controls

- `tenant_id` is `NOT NULL` on every tenant-owned table, with composite indexes that lead with
  `tenant_id`.
- Repositories never expose an unscoped read.
- Cross-tenant isolation is covered by automated tests.
