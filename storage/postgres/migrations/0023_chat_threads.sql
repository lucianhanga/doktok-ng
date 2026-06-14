-- M6.4 #248: server-side conversation persistence so chats survive a reload and appear in a thread
-- list. Tenant-scoped; messages cascade-delete with their thread. Additive - the chat endpoint still
-- works without a thread_id (client-held history), so this is non-breaking.

CREATE TABLE IF NOT EXISTS chat_threads (
    id          text PRIMARY KEY,
    tenant_id   text NOT NULL,
    title       text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- List a tenant's threads most-recently-active first.
CREATE INDEX IF NOT EXISTS idx_chat_threads_tenant_updated
    ON chat_threads (tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          text PRIMARY KEY,
    thread_id   text NOT NULL REFERENCES chat_threads (id) ON DELETE CASCADE,
    tenant_id   text NOT NULL,
    role        text NOT NULL,  -- 'user' | 'assistant'
    content     text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Read a thread's messages in order.
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread_created
    ON chat_messages (thread_id, created_at);
