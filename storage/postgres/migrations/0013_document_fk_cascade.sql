-- Make a document's child rows cascade-delete with it, so delete/reingest fully purges a document
-- (chunks, entities, features, category links) instead of orphaning them. extracted_records already
-- cascades (migration 0012). Drop any pre-existing orphans first so the constraints can be added.
DELETE FROM document_chunks WHERE document_id NOT IN (SELECT id FROM documents);
DELETE FROM document_entities WHERE document_id NOT IN (SELECT id FROM documents);
DELETE FROM document_features WHERE document_id NOT IN (SELECT id FROM documents);
DELETE FROM document_category_links WHERE document_id NOT IN (SELECT id FROM documents);

ALTER TABLE document_chunks
    ADD CONSTRAINT fk_chunks_document FOREIGN KEY (document_id)
    REFERENCES documents(id) ON DELETE CASCADE;
ALTER TABLE document_entities
    ADD CONSTRAINT fk_entities_document FOREIGN KEY (document_id)
    REFERENCES documents(id) ON DELETE CASCADE;
ALTER TABLE document_features
    ADD CONSTRAINT fk_features_document FOREIGN KEY (document_id)
    REFERENCES documents(id) ON DELETE CASCADE;
ALTER TABLE document_category_links
    ADD CONSTRAINT fk_doc_cat_links_document FOREIGN KEY (document_id)
    REFERENCES documents(id) ON DELETE CASCADE;
