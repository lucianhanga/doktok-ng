-- Raise per-document category cap from 5 to 8 and per-tenant cap from 20 to 50.
-- Both functions use CREATE OR REPLACE so the migration is idempotent.

CREATE OR REPLACE FUNCTION enforce_doc_category_cap() RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('doccat:' || NEW.tenant_id || ':' || NEW.document_id));
    IF (SELECT count(*) FROM document_category_links
        WHERE tenant_id = NEW.tenant_id AND document_id = NEW.document_id) >= 8 THEN
        RAISE EXCEPTION 'document % already has 8 categories', NEW.document_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION enforce_category_cap() RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('cat:' || NEW.tenant_id));
    IF NEW.status = 'active' AND (SELECT count(*) FROM categories
        WHERE tenant_id = NEW.tenant_id AND status = 'active') >= 50 THEN
        RAISE EXCEPTION 'tenant % already has 50 active categories', NEW.tenant_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
