-- ADR-0022 Phase 3 (STM): a rolling per-conversation summary so long threads don't blow the model
-- context window. ``summary`` is the folded text of the older turns; ``summary_through`` is the
-- watermark - the number of leading messages already folded into it - so each turn only summarizes
-- the new overflow. Additive + idempotent; old rows default to an empty summary (no compaction).
ALTER TABLE chat_threads
    ADD COLUMN IF NOT EXISTS summary text NOT NULL DEFAULT '';
ALTER TABLE chat_threads
    ADD COLUMN IF NOT EXISTS summary_through integer NOT NULL DEFAULT 0;
