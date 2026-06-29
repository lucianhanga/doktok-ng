-- KAG alias-folding tier: stable cross-document entity alias resolution (conservative, containment).
-- Folds surface variants into one canonical node ('M-net' -> 'M-net Telekommunikations GmbH') when
-- the alias is a unique-longest token-prefix of a same-type node. Type-respecting, deterministic.
--
-- WHY a table (not just a resolution-time merge): Phase-1 EntityGraphFeature re-derives node ids
-- deterministically per document, so a deleted alias node would be re-created on the next reprocess
-- (un-merging it). This table makes the merge STABLE: the resolve path maps a mention's
-- (tenant, entity_type, normalized_value) to its canonical node here first, so a future 'M-net'
-- mention points straight at the canonical node and the merge survives re-ingestion.
--
-- Additive only. Clean rollback: DROP TABLE kg_entity_aliases. document_entities + Phase-1/2 tables
-- are untouched.

CREATE TABLE IF NOT EXISTS kg_entity_aliases (
    tenant_id           text NOT NULL,
    entity_type         text NOT NULL,
    alias_normalized    text NOT NULL,            -- the folded surface form (a node's normalized_value)
    canonical_entity_id text NOT NULL
        REFERENCES kg_entities (id) ON DELETE CASCADE,
    created_at          timestamptz NOT NULL DEFAULT now(),
    -- One canonical per (tenant, type, surface form). Also the lookup key for alias-aware resolve.
    -- Never folds across entity_type (type is part of the key).
    PRIMARY KEY (tenant_id, entity_type, alias_normalized)
);

-- Reverse lookup: "which aliases fold into this node" (re-point on a chained merge; cascade target).
CREATE INDEX IF NOT EXISTS idx_kg_aliases_tenant_canon
    ON kg_entity_aliases (tenant_id, canonical_entity_id);
