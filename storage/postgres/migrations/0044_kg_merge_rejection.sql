-- #530: persist rejected merge suggestions so they are not re-proposed (learn from rejections).
--
-- When a user rejects a suggested merge in the review queue, that decision must STICK - otherwise
-- the deterministic cascade re-proposes the same pair on the next fetch (and, for a fuzzy pair, it
-- may even re-hit the LLM). This table records the rejection keyed on the SAME normalized,
-- order-independent pair key the adjudication cache uses (``merge_adjudication_pair_key``), so it
-- survives a KG rebuild that re-mints node ids and matches the pair regardless of direction.
--
-- The merge-suggestions endpoint consults this set and drops any rejected pair before adjudication.
-- Additive + idempotent. Rollback: DROP TABLE kg_merge_rejection;

CREATE TABLE IF NOT EXISTS kg_merge_rejection (
    tenant_id  text NOT NULL,
    pair_key   text NOT NULL,
    actor      text NOT NULL DEFAULT 'user',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, pair_key)
);
