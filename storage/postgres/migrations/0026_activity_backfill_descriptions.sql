-- M8: one-time enrichment of the legacy activity rows that 0025 backfilled from audit_events.
-- Those rows were inserted with an empty description (and, for older data, possibly without the
-- derived phase/severity). Give them the SAME phase/severity/description the live logger now
-- writes for new events, so historical and new rows read identically in the Activity table.
--
-- Idempotent and narrow: only touches the six legacy ingestion event types whose description is
-- still blank, so re-running (or running after new rows exist) changes nothing. The human-readable
-- strings mirror core/doktok_core/audit/logger.py::_EVENT_DEFAULTS.
UPDATE document_activity SET
    phase = CASE event_type
        WHEN 'document.received' THEN 'intake'
        WHEN 'document.identified' THEN 'intake'
        WHEN 'document.duplicate' THEN 'intake'
        WHEN 'document.activated' THEN 'index'
        WHEN 'document.quarantined' THEN 'intake'
        WHEN 'document.failed' THEN 'intake'
        ELSE phase
    END,
    severity = CASE event_type
        WHEN 'document.failed' THEN 'error'
        WHEN 'document.quarantined' THEN 'warning'
        ELSE severity
    END,
    description = CASE event_type
        WHEN 'document.received' THEN 'Document received for processing'
        WHEN 'document.identified' THEN 'Document identified and accepted'
        WHEN 'document.duplicate' THEN 'Duplicate of existing document'
        WHEN 'document.activated' THEN 'Document activated and searchable'
        WHEN 'document.quarantined' THEN 'Document quarantined'
        WHEN 'document.failed' THEN 'Document processing failed'
        ELSE description
    END
WHERE (description IS NULL OR description = '')
  AND event_type IN (
        'document.received', 'document.identified', 'document.duplicate',
        'document.activated', 'document.quarantined', 'document.failed'
  );
