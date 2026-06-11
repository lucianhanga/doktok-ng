-- M6.2 E2: multi-label categories with a bounded controlled vocabulary.
-- Caps enforced in the DB (not the prompt): <=5 categories per document, <=20 active per tenant.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS categories (
    id          text PRIMARY KEY,
    tenant_id   text NOT NULL,
    name        text NOT NULL,            -- canonical display name
    normalized  text NOT NULL,            -- casefolded/de-pluralized slug for dedup
    status      text NOT NULL DEFAULT 'active',   -- active | merged | deprecated
    merged_into text,                      -- surviving category when status='merged' (E3)
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, normalized)
);
CREATE INDEX IF NOT EXISTS idx_categories_tenant_status ON categories (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_categories_norm_trgm ON categories USING gin (normalized gin_trgm_ops);

CREATE TABLE IF NOT EXISTS document_category_links (
    tenant_id   text NOT NULL,
    document_id text NOT NULL,
    category_id text NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, document_id, category_id)
);
CREATE INDEX IF NOT EXISTS idx_doc_cat_links_cat ON document_category_links (tenant_id, category_id);

-- Cap: at most 5 categories per document. Advisory lock serializes only same-document writers, so
-- concurrent classification of different documents never contends; race-safe at read-committed.
CREATE OR REPLACE FUNCTION enforce_doc_category_cap() RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('doccat:' || NEW.tenant_id || ':' || NEW.document_id));
    IF (SELECT count(*) FROM document_category_links
        WHERE tenant_id = NEW.tenant_id AND document_id = NEW.document_id) >= 5 THEN
        RAISE EXCEPTION 'document % already has 5 categories', NEW.document_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_doc_category_cap ON document_category_links;
CREATE TRIGGER trg_doc_category_cap BEFORE INSERT ON document_category_links
    FOR EACH ROW EXECUTE FUNCTION enforce_doc_category_cap();

-- Cap: at most 20 active categories per tenant. Lock is per-tenant so two workers can't both create
-- the 20th category.
CREATE OR REPLACE FUNCTION enforce_category_cap() RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('cat:' || NEW.tenant_id));
    IF NEW.status = 'active' AND (SELECT count(*) FROM categories
        WHERE tenant_id = NEW.tenant_id AND status = 'active') >= 20 THEN
        RAISE EXCEPTION 'tenant % already has 20 active categories', NEW.tenant_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_category_cap ON categories;
CREATE TRIGGER trg_category_cap BEFORE INSERT ON categories
    FOR EACH ROW EXECUTE FUNCTION enforce_category_cap();
