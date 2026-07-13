-- #609: persist "not family" dismissals for the shared-surname hint panel (#532 follow-up).
--
-- A shared surname is a weak hint, so there are false pairs (unrelated people who happen to share a
-- surname). This store lets a user permanently dismiss a pair so the family-suggestion GET never
-- re-offers it - mirroring the merge-rejection store (0044), one tier down.
--
-- pair_key is the two canonical PERSON ids sorted and joined by '|', so it is order-independent and
-- matches the confirm endpoint's canonicalization (confirming or dismissing A/B == B/A).
--
-- Additive + idempotent. Rollback: DROP TABLE kg_family_dismissal.
CREATE TABLE IF NOT EXISTS kg_family_dismissal (
    tenant_id  text NOT NULL,
    pair_key   text NOT NULL,
    actor      text NOT NULL DEFAULT 'user',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, pair_key)
);
