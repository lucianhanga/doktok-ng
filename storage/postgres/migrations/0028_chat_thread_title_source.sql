-- M8 chat overhaul: distinguish an auto-derived chat title from a user-set one, so once a user
-- renames a thread the first-message auto-seed never overwrites it again. Additive + idempotent;
-- existing rows default to 'auto' (their titles keep auto-seeding as before).
ALTER TABLE chat_threads
    ADD COLUMN IF NOT EXISTS title_source text NOT NULL DEFAULT 'auto';
