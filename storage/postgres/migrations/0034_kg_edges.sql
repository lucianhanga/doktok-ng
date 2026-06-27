-- KAG Phase 2: relation edges
-- kg_edges: one row per distinct directed triple; UNIQUE(tenant_id, src_entity_id, predicate, dst_entity_id)
-- src/dst are canonical_entity_ids from kg_entities. FK ON DELETE CASCADE so orphaned edges auto-clean.
-- evidence_count: denormalized count of provenance rows (updated on insert/delete of provenance).
-- Additive only. Clean rollback: DROP TABLE kg_edge_provenance; DROP TABLE kg_edges;

CREATE TABLE IF NOT EXISTS kg_edges (
    id               text PRIMARY KEY,  -- uuid5 of (tenant|src|predicate|dst)
    tenant_id        text NOT NULL,
    src_entity_id    text NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    predicate        text NOT NULL,
    dst_entity_id    text NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    evidence_count   int  NOT NULL DEFAULT 0,
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, src_entity_id, predicate, dst_entity_id)
);

-- Traversal: find all edges starting from a node (outbound)
CREATE INDEX IF NOT EXISTS idx_kg_edges_tenant_src ON kg_edges(tenant_id, src_entity_id);
-- Traversal: find all edges ending at a node (inbound)
CREATE INDEX IF NOT EXISTS idx_kg_edges_tenant_dst ON kg_edges(tenant_id, dst_entity_id);

CREATE TABLE IF NOT EXISTS kg_edge_provenance (
    id           text PRIMARY KEY,  -- uuid4
    tenant_id    text NOT NULL,
    edge_id      text NOT NULL REFERENCES kg_edges(id) ON DELETE CASCADE,
    document_id  text NOT NULL,
    chunk_id     text,              -- nullable (window-level provenance)
    evidence     text NOT NULL,     -- verbatim sentence(s) from source text
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- "which provenance rows cite this edge" + deletion propagation
CREATE INDEX IF NOT EXISTS idx_kg_edge_prov_edge ON kg_edge_provenance(edge_id);
-- "which edges does this document contribute" (replace-by-document pattern)
CREATE INDEX IF NOT EXISTS idx_kg_edge_prov_tenant_doc ON kg_edge_provenance(tenant_id, document_id);
