-- #514: drop the stale kg_entities UNIQUE (tenant_id, entity_type, normalized_value).
--
-- Node identity moved to the TOKEN-SORTED-key id in #508 (canonical_entity_id = uuid5 over
-- normalize_entity_name(value)), but this legacy constraint still guards the first-seen, UNSORTED
-- surface form stored in normalized_value. On a KG reprocess a mention gets a NEW sorted-key id
-- that differs from a pre-#508 node holding the SAME normalized_value; the upsert's
-- ON CONFLICT (id) DO NOTHING misses (ids differ), so the INSERT violates this unique constraint:
--   duplicate key value violates unique constraint
--   "kg_entities_tenant_id_entity_type_normalized_value_key"
-- The id (primary key) and this constraint are simply out of sync.
--
-- Fix: drop the constraint. The primary key (id, which now encodes the sorted-key identity) is the
-- authoritative uniqueness guarantee. Two nodes may legitimately share a normalized_value during
-- the old->new transition, until the reprocess's orphan-prune (prune_orphan_entities) collapses the
-- now-evidenceless old-id node once its mentions move to the sorted-key canonical.
--
-- Replace it with a NON-unique btree so equality/prefix lookups on the triple stay indexed; free-
-- text search continues to use the pg_trgm GIN index on normalized_value from 0041.
--
-- Additive + idempotent. Deliberately one-way: re-adding the UNIQUE could fail once legitimate
-- sorted-key duplicates exist, and the constraint is provably wrong under sorted-key identity.

ALTER TABLE kg_entities
    DROP CONSTRAINT IF EXISTS kg_entities_tenant_id_entity_type_normalized_value_key;

CREATE INDEX IF NOT EXISTS idx_kg_entities_tenant_type_value
    ON kg_entities (tenant_id, entity_type, normalized_value);
