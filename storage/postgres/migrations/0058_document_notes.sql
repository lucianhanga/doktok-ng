-- #736: user-authored, timestamped notes on documents. Entries are immutable (no updated_at);
-- deletions are audit-logged (the audit row carries a body snapshot). Cascade with the document.
CREATE TABLE document_notes (
  id            text PRIMARY KEY,
  tenant_id     text NOT NULL,
  document_id   text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  author_id     text NOT NULL,
  author_email  text NOT NULL,
  body          text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_document_notes_doc ON document_notes (tenant_id, document_id, created_at DESC);
