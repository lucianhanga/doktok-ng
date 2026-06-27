-- M-KAG Phase 1 (#KAG-A): cross-document entity-node graph (KAG "Path A", deterministic tier).
-- Additive only - `document_entities` is untouched; a clean DROP TABLE of both tables (mentions
-- first, then entities) is a full rollback. Tenant-scoped throughout (ADR-0007/0008).
--
-- Two tables:
--   kg_entities          - canonical cross-document entity nodes. One node per distinct
--                          (tenant_id, entity_type, normalized_value). The id is a deterministic
--                          uuid5 of exactly that triple (see knowledge_graph/resolve.py), so two
--                          documents mentioning the same normalized entity map to the SAME node
--                          with no cross-document clustering - the Phase-1 deterministic guarantee.
--   kg_entity_mentions   - links each `document_entities` mention to its canonical node, with
--                          provenance (document_id, chunk_id). PK = mention_id, so resolution is
--                          idempotent (re-running a document replaces its mention rows in place).
--
-- DEFERRED to Phase 2 (NOT created here): the pgvector-fuzzy resolution tier (an `embedding`
-- column whose dimension the LLM specialist still owns) and the relation/edge tables
-- (kg_edges / kg_edge_provenance), whose predicate vocabulary is still being designed.

CREATE TABLE IF NOT EXISTS kg_entities (
    id               text PRIMARY KEY,         -- uuid5(tenant_id | entity_type | normalized_value)
    tenant_id        text NOT NULL,
    entity_type      text NOT NULL,
    normalized_value text NOT NULL,            -- the resolution key (== canonical surface form)
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    -- One node per normalized entity per type per tenant. Also the upsert conflict target and the
    -- lookup index for "find the node for this (type, value)". Never merge across entity_type.
    UNIQUE (tenant_id, entity_type, normalized_value)
);

CREATE TABLE IF NOT EXISTS kg_entity_mentions (
    mention_id          text PRIMARY KEY
        REFERENCES document_entities (id) ON DELETE CASCADE,   -- 1 row per resolved mention
    tenant_id           text NOT NULL,
    canonical_entity_id text NOT NULL
        REFERENCES kg_entities (id) ON DELETE CASCADE,
    document_id         text NOT NULL,                          -- provenance
    chunk_id            text,                                   -- provenance (nullable)
    entity_type         text NOT NULL,
    normalized_value    text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- Replace-by-document (idempotent reconcile) + "which mentions does this document contribute".
CREATE INDEX IF NOT EXISTS idx_kg_mentions_tenant_doc
    ON kg_entity_mentions (tenant_id, document_id);
-- "which documents/mentions resolve to this node" (cross-doc merge readout + Phase-3 traversal seed).
CREATE INDEX IF NOT EXISTS idx_kg_mentions_tenant_canon
    ON kg_entity_mentions (tenant_id, canonical_entity_id);
