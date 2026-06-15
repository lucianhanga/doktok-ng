-- M8.x (#312): stop keeping low-value entity types. The regex extractor that produced MONEY / DATE
-- / INVOICE_ID / CONTRACT_ID / DOCUMENT_ID matched ~90% noise (monetary data lives in extracted
-- records, dates in document metadata), so drop existing rows of those types. Going forward the
-- extractor no longer emits them. Idempotent.
DELETE FROM document_entities
WHERE entity_type IN ('MONEY', 'DATE', 'INVOICE_ID', 'CONTRACT_ID', 'DOCUMENT_ID');
