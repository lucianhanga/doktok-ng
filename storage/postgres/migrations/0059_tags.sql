-- Epic #543 / #544: manual, tenant-level document tags. Mirrors the categories 0011 pattern
-- (normalized dedup key, trigram index, advisory-lock cap triggers). The `scope`/`owner_user_id`
-- columns are the forward seam for FUTURE personal tags (v1 ships tenant scope only).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS tags (
    id            text PRIMARY KEY,
    tenant_id     text NOT NULL,
    name          text NOT NULL,            -- display name (emoji allowed)
    normalized    text NOT NULL,            -- NFKC + casefold + trimmed + collapsed ws (emoji stripped)
    description   text NOT NULL DEFAULT '',
    color         text NOT NULL DEFAULT 'slate',  -- palette TOKEN (not freeform hex; WCAG per theme)
    status        text NOT NULL DEFAULT 'active', -- active | merged | deprecated
    merged_into   text,                          -- surviving tag when status='merged'
    scope         text NOT NULL DEFAULT 'tenant',  -- tenant | user (future; v1 = tenant only)
    owner_user_id text,                          -- nullable; the personal-tag owner in a future epic
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, normalized)
);
CREATE INDEX IF NOT EXISTS idx_tags_tenant_status ON tags (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_tags_norm_trgm ON tags USING gin (normalized gin_trgm_ops);
-- Forward seam: personal tags are unique per owner; tenant tags share one bucket ('').
CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_owner_scope
    ON tags (tenant_id, scope, COALESCE(owner_user_id, ''), normalized);

CREATE TABLE IF NOT EXISTS document_tags (
    tenant_id   text NOT NULL,
    document_id text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tag_id      text NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, document_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_document_tags_tag ON document_tags (tenant_id, tag_id);

-- Cap: at most 100 active tags per tenant (per-tenant advisory lock serializes creators).
CREATE OR REPLACE FUNCTION enforce_tag_cap() RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('tag:' || NEW.tenant_id));
    IF NEW.status = 'active' AND (SELECT count(*) FROM tags
        WHERE tenant_id = NEW.tenant_id AND status = 'active') >= 100 THEN
        RAISE EXCEPTION 'tenant % already has 100 active tags', NEW.tenant_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tag_cap ON tags;
CREATE TRIGGER trg_tag_cap BEFORE INSERT ON tags
    FOR EACH ROW EXECUTE FUNCTION enforce_tag_cap();

-- Cap: at most 20 tags per document (per-document advisory lock, race-safe at read-committed).
CREATE OR REPLACE FUNCTION enforce_document_tag_cap() RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('doctag:' || NEW.tenant_id || ':' || NEW.document_id));
    IF (SELECT count(*) FROM document_tags
        WHERE tenant_id = NEW.tenant_id AND document_id = NEW.document_id) >= 20 THEN
        RAISE EXCEPTION 'document % already has 20 tags', NEW.document_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_document_tag_cap ON document_tags;
CREATE TRIGGER trg_document_tag_cap BEFORE INSERT ON document_tags
    FOR EACH ROW EXECUTE FUNCTION enforce_document_tag_cap();
