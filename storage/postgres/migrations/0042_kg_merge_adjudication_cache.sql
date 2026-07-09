-- KG merge-suggestion adjudication cache (#535): stop re-running the LLM on every
-- GET /merge-suggestions.
--
-- Root cause being fixed: adjudicate_suggestions() called adjudicator.adjudicate() for every
-- non-token_set pair on EVERY request, so each switch to the Insights tab fired a burst of LLM
-- calls even when nothing changed. This table caches each pair's verdict so a repeat GET with
-- unchanged candidates makes ZERO LLM calls.
--
-- Key design (why it survives graph rebuilds): the row is keyed on a NORMALIZED, order-independent
-- pair_key (sorted normalize_entity_name(a) + SEP + normalize_entity_name(b)), NOT on the volatile
-- node uuids. KG node ids are re-derived from the same normalization on every reprocess, so the
-- entity_graph/relations rebuild that re-mints suggestion rows produces the SAME pair_key - the
-- cached verdict is reused instead of re-adjudicated. method + score_bucket (score rounded to 2
-- decimals) are part of the key so a genuinely different match tier or a real score change
-- re-adjudicates, while tiny trigram score drift does not thrash the cache.
--
-- Additive + idempotent. Rollback: DROP TABLE kg_merge_adjudication.
--
-- Note (#530, not built here): a future per-pair REJECTION store composes cleanly on top of this
-- pair_key - the rejection check would key on the same normalized pair before the cache lookup.

CREATE TABLE IF NOT EXISTS kg_merge_adjudication (
    tenant_id    text NOT NULL,
    pair_key     text NOT NULL,          -- sorted(normalize_entity_name(a), normalize_entity_name(b)) joined
    method       text NOT NULL,          -- 'fuzzy_trgm' | 'token_subset' | 'token_typo' | ... (never 'token_set')
    score_bucket text NOT NULL,          -- score rounded to 2 decimals, as text (stable cache key across drift)
    same         boolean NOT NULL,       -- the LLM verdict: same real-world entity?
    confidence   real NOT NULL,          -- 0-1
    reason       text NOT NULL,          -- one-sentence explanation
    canonical    text,                   -- LLM-preferred canonical display name (nullable)
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, pair_key, method, score_bucket)
);
