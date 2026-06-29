-- ADR-0022: persist the per-turn activity trace (the agent's tool/pipeline step labels) so a
-- reopened conversation re-shows how each answer was built (the composition bar), not just the
-- answer. Additive + idempotent; old rows default to an empty trace.
ALTER TABLE chat_messages
    ADD COLUMN IF NOT EXISTS steps jsonb NOT NULL DEFAULT '[]'::jsonb;
