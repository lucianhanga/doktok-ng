-- ADR-0022: long-term semantic memory. Salient facts from past conversations, embedded so a later
-- turn can recall them across threads. Opt-in per turn (default off = private); never written for
-- an incognito turn. ``superseded`` lets a correction retire an old memory without deleting it.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chat_memories (
    id          text PRIMARY KEY,
    tenant_id   text NOT NULL,
    kind        text NOT NULL DEFAULT 'conversation',
    text        text NOT NULL,
    embedding   vector(1024),
    confidence  real NOT NULL DEFAULT 1.0,
    superseded  boolean NOT NULL DEFAULT false,
    source      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_memories_tenant ON chat_memories (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_memories_embedding
    ON chat_memories USING hnsw (embedding vector_cosine_ops);
