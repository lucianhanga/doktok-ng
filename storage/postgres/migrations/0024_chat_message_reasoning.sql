-- M6.5: persist a chat assistant turn's reasoning + source citations alongside its content, so a
-- resumed or reloaded thread re-shows the model's reasoning and the top-documents (sources) list
-- instead of losing them (they were previously in-memory only). Additive + idempotent.

ALTER TABLE chat_messages
    ADD COLUMN IF NOT EXISTS reasoning text NOT NULL DEFAULT '';

ALTER TABLE chat_messages
    ADD COLUMN IF NOT EXISTS citations jsonb NOT NULL DEFAULT '[]'::jsonb;
