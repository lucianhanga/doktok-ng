-- M8 chat overhaul: persist per-assistant-message ranking trace + token/timing metrics so a
-- reloaded thread re-shows the winning chunks and the per-turn/per-chat figures. Additive +
-- idempotent; jsonb keeps the evolving shape out of the schema. Old rows default to empty.
ALTER TABLE chat_messages
    ADD COLUMN IF NOT EXISTS ranking jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE chat_messages
    ADD COLUMN IF NOT EXISTS metrics jsonb NOT NULL DEFAULT '{}'::jsonb;
