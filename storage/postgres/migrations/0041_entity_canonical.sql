-- KG entity resolution Wave 1 (#508): canonical/alias node model + merge audit log + fuzzy index.
--
-- Root cause being fixed: node identity is uuid5(tenant | type | normalized_value) with only
-- casefold+whitespace normalization, so surface variants ('lucian hanga' / 'hanga,lucian' /
-- 'lucianhanga') each become a distinct node. This migration adds the DATA MODEL for reversible
-- merges; the matching cascade lives in core (knowledge_graph/entity_resolution.py).
--
-- Three changes, all additive + idempotent:
--   1. kg_entities.canonical_id  - a node is CANONICAL when canonical_id IS NULL (or = id);
--                                  an ALIAS node points at its canonical. Unlike the alias-fold
--                                  tier (0035), a merged node is KEPT (not deleted) so the merge
--                                  is reversible via split.
--   2. kg_entity_aliases         - EXTENDED (table exists since 0035) with surface_form /
--                                  source / confidence so every surface form a canonical owns is
--                                  persisted with provenance. Existing rows keep working: the
--                                  alias-aware resolve path keys on
--                                  (tenant_id, entity_type, alias_normalized) which is unchanged.
--   3. kg_entity_merge_log       - who/when/method/score for every merge AND split, so a merge
--                                  is auditable and reversible. No FK to kg_entities: the log
--                                  must survive node deletion (purge/rollback).
-- Plus the missing pg_trgm GIN index on kg_entities.normalized_value - the index behind
-- find_similar_entities() and the bounded merge-suggestion blocking query.
--
-- Back-compat: existing node ids are NOT rewritten. New writes derive the node key from the
-- token-sorted normalized value (core/knowledge_graph/resolve.py); collapsing pre-existing
-- variant nodes requires a KG feature reprocess (entity_graph + relations).
-- Rollback: DROP TABLE kg_entity_merge_log; ALTER TABLE kg_entities DROP COLUMN canonical_id;
-- ALTER TABLE kg_entity_aliases DROP COLUMN surface_form, DROP COLUMN source,
-- DROP COLUMN confidence; DROP INDEX idx_kg_entities_normvalue_trgm.

-- 1. Canonical pointer. ON DELETE SET NULL: deleting a canonical promotes its aliases back to
--    standalone canonicals instead of failing or cascading node deletes.
ALTER TABLE kg_entities
    ADD COLUMN IF NOT EXISTS canonical_id text
        REFERENCES kg_entities (id) ON DELETE SET NULL;

-- "Which nodes are folded into this canonical" (split UI, chain flattening on merge).
CREATE INDEX IF NOT EXISTS idx_kg_entities_tenant_canonical
    ON kg_entities (tenant_id, canonical_id)
    WHERE canonical_id IS NOT NULL;

-- 2. Alias provenance columns. surface_form is the original (pre-normalization) display form of
--    the folded node; source records which cascade stage produced the merge
--    ('containment' = legacy 0035 fold tier, 'token_set' / 'fuzzy_trgm' / 'manual' = Wave 1+).
ALTER TABLE kg_entity_aliases
    ADD COLUMN IF NOT EXISTS surface_form text,
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'containment',
    ADD COLUMN IF NOT EXISTS confidence real;

-- 3. Merge/split audit log. action: 'merge' | 'split'. method: cascade stage or 'manual'.
CREATE TABLE IF NOT EXISTS kg_entity_merge_log (
    id           text PRIMARY KEY,
    tenant_id    text NOT NULL,
    action       text NOT NULL CHECK (action IN ('merge', 'split')),
    canonical_id text NOT NULL,             -- the surviving canonical node id
    alias_id     text NOT NULL,             -- the node merged in (or promoted back out)
    method       text NOT NULL,             -- 'token_set' | 'fuzzy_trgm' | 'manual' | ...
    score        real,                      -- match score (NULL for manual/split)
    actor        text NOT NULL DEFAULT 'system',
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Per-tenant audit readout, newest first.
CREATE INDEX IF NOT EXISTS idx_kg_merge_log_tenant_created
    ON kg_entity_merge_log (tenant_id, created_at DESC);

-- 4. The fuzzy-matching index. pg_trgm is already installed (0011), the CREATE EXTENSION is a
--    defensive no-op. Trigram sets are word-based, so similarity() is word-order-insensitive -
--    exactly what entity surface variants need.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_kg_entities_normvalue_trgm
    ON kg_entities USING gin (normalized_value gin_trgm_ops);
