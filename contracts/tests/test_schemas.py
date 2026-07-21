import json
from datetime import UTC, datetime

from doktok_contracts.schemas import (
    AiSettingsResponse,
    AuditEventType,
    BackupEvent,
    ChatEvent,
    ConfidenceBuckets,
    Document,
    DocumentDetail,
    DocumentRecordSummary,
    DocumentStatus,
    DrpHistoryResponse,
    ExtractedRecord,
    IngestionJob,
    JobStatus,
    MerchantRollup,
    RecordCurrencyRollup,
    TenantAiSettings,
    TenantAiSettingsUpdate,
    TraceStep,
)


def test_document_defaults() -> None:
    doc = Document(
        id="doc-1",
        tenant_id="t1",
        sha256="abc",
        original_filename="invoice.pdf",
        created_at=datetime.now(UTC),
    )
    assert doc.status is DocumentStatus.PROCESSING
    assert doc.metadata == {}


def test_ingestion_job_defaults_to_queued() -> None:
    job = IngestionJob(id="job-1", tenant_id="t1", source_path="/ingest/invoice.pdf")
    assert job.status is JobStatus.QUEUED


def test_job_status_state_machine_values() -> None:
    expected = {
        "queued",
        "detecting",
        "hashing",
        "normalizing",
        "extracting",
        "chunking",
        "embedding",
        "indexing",
        "activating",
        "active",
        "failed",
        "quarantined",
        "duplicate",
    }
    assert {s.value for s in JobStatus} == expected


def test_backup_event_parses_real_history_line_with_extra_keys() -> None:
    # A real host-written history.jsonl line carries tamper-evidence fields (schema/seq/prev_sha256)
    # that the wire model must IGNORE, plus the whitelisted metric fields it surfaces.
    line = (
        '{"schema":1,"seq":42,"prev_sha256":"abc123","ts":"2026-06-26T03:00:00Z",'
        '"leg":"files","event":"success","ok":true,"size":"662 MiB","item_count":287,'
        '"backup_id":"a1b2c3","duration_ms":48213,"detail":"restic snapshot"}'
    )
    ev = BackupEvent.model_validate(json.loads(line))
    assert ev.leg == "files" and ev.event == "success" and ev.ok is True
    assert ev.size == "662 MiB" and ev.item_count == 287 and ev.backup_id == "a1b2c3"
    assert ev.duration_ms == 48213 and ev.detail == "restic snapshot" and ev.seq == 42
    # prev_sha256/schema are not fields on the model, so they are dropped on the wire.
    assert "prev_sha256" not in ev.model_dump() and "schema" not in ev.model_dump()


def test_backup_event_defaults() -> None:
    ev = BackupEvent(ts=datetime.now(UTC), leg="pg", event="start")
    assert ev.ok is False
    assert ev.size == "" and ev.backup_id == "" and ev.detail == ""
    assert ev.item_count is None and ev.duration_ms is None and ev.seq is None


def test_drp_history_response_defaults() -> None:
    resp = DrpHistoryResponse()
    assert resp.events == [] and resp.source_available is False
    assert resp.total_returned == 0 and resp.truncated is False and resp.integrity_ok is True


def test_backup_audit_event_types_present() -> None:
    assert AuditEventType.BACKUP_COMPLETED.value == "backup.completed"
    assert AuditEventType.BACKUP_FAILED.value == "backup.failed"
    assert AuditEventType.DRILL_COMPLETED.value == "drill.completed"


def test_extracted_record_confidence_defaults_to_none() -> None:
    # Honest default: a never-scored row is UNSCORED (None), not falsely 1.0/"100% confident".
    rec = ExtractedRecord(id="r1", tenant_id="t1", document_id="d1", raw_text="x")
    assert rec.confidence is None


def test_document_detail_round_trip_without_records() -> None:
    # Backward-compat: an older payload with no `records` key still validates, defaulting to empty.
    doc = Document(
        id="d1",
        tenant_id="t1",
        sha256="abc",
        original_filename="note.txt",
        created_at=datetime.now(UTC),
    )
    detail = DocumentDetail.model_validate({"document": doc.model_dump()})
    assert detail.records == DocumentRecordSummary()
    assert detail.records.total == 0 and detail.records.by_currency == []
    # And it round-trips through JSON with the additive field present + empty.
    again = DocumentDetail.model_validate(json.loads(detail.model_dump_json()))
    assert again.records.total == 0


def test_document_detail_round_trip_with_records() -> None:
    doc = Document(
        id="d1",
        tenant_id="t1",
        sha256="abc",
        original_filename="statement.pdf",
        created_at=datetime.now(UTC),
    )
    summary = DocumentRecordSummary(
        total=3,
        by_currency=[
            RecordCurrencyRollup(currency="EUR", debit_minor=8240, credit_minor=0, count=3)
        ],
        top_merchants=[
            MerchantRollup(merchant="block house", count=2, total_minor=8240, currency="EUR")
        ],
        confidence=ConfidenceBuckets(unscored=3),
        low_confidence_count=0,
    )
    detail = DocumentDetail(document=doc, records=summary)
    again = DocumentDetail.model_validate(json.loads(detail.model_dump_json()))
    assert again.records.total == 3
    assert again.records.by_currency[0].debit_minor == 8240
    assert again.records.confidence.unscored == 3
    assert again.records.top_merchants[0].merchant == "block house"


# ---------------------------------------------------------------------------
# TraceStep contract (Phase 1 #495: multi-agent trace richness)
# ---------------------------------------------------------------------------


def test_trace_step_defaults_are_backward_compatible() -> None:
    # A minimal legacy step (kind + label only) must still parse and have None enrichment fields.
    s = TraceStep(kind="retrieve", label="Searching your documents")
    assert s.role is None
    assert s.verdict is None
    assert s.attempt is None
    assert s.detail == ""
    assert s.at is None


def test_trace_step_old_dict_without_enrichment_fields_still_parses() -> None:
    # Simulates a row stored before the enrichment fields were added (no keys in JSON).
    old_payload = {"kind": "step", "label": "Some step"}
    s = TraceStep.model_validate(old_payload)
    assert s.role is None and s.verdict is None and s.attempt is None


def test_trace_step_new_fields_round_trip() -> None:
    # A fully enriched step serialises all new fields and round-trips correctly.
    s = TraceStep(
        kind="verification",
        label="Verification complete",
        role="verifier",
        verdict="pass",
        attempt=None,
    )
    payload = json.loads(s.model_dump_json())
    again = TraceStep.model_validate(payload)
    assert again.kind == "verification"
    assert again.role == "verifier"
    assert again.verdict == "pass"
    assert again.attempt is None


def test_trace_step_draft_with_attempt() -> None:
    s = TraceStep(kind="draft", label="Draft 2", role="researcher", attempt=2)
    payload = json.loads(s.model_dump_json())
    again = TraceStep.model_validate(payload)
    assert again.attempt == 2
    assert again.role == "researcher"


def test_trace_step_embedded_in_chat_event_includes_new_fields() -> None:
    # The ChatEvent SSE serialisation must forward role/verdict/attempt through model_dump().
    ts = TraceStep(
        kind="verification", label="Verification complete", role="verifier", verdict="revise"
    )
    event = ChatEvent(type="step", delta=ts.label, trace_step=ts)
    dumped = event.model_dump()
    assert dumped["trace_step"] is not None
    assert dumped["trace_step"]["role"] == "verifier"
    assert dumped["trace_step"]["verdict"] == "revise"
    assert dumped["trace_step"]["attempt"] is None


def test_tenant_ai_settings_update_accepts_write_only_key() -> None:
    update = TenantAiSettingsUpdate(openai_api_key="sk-x", no_egress=False)
    assert update.openai_api_key == "sk-x"
    assert update.no_egress is False
    # None = leave the stored tenant key unchanged (the default).
    assert TenantAiSettingsUpdate().openai_api_key is None


def test_tenant_ai_settings_stays_secret_free() -> None:
    # The override contract is returned by GET /settings/ai - the key must NOT live on it (#719).
    assert "openai_api_key" not in TenantAiSettings.model_fields


def test_ai_settings_response_reports_tenant_key_flag_only() -> None:
    assert AiSettingsResponse().tenant_openai_api_key_set is False
    resp = AiSettingsResponse(tenant_openai_api_key_set=True)
    assert resp.tenant_openai_api_key_set is True
    # Write-only everywhere: no response model carries a key field.
    assert "openai_api_key" not in AiSettingsResponse.model_fields


def test_document_title_source_defaults_to_auto() -> None:
    doc = Document(
        id="d1",
        tenant_id="t1",
        sha256="x",
        original_filename="a.pdf",
        created_at=datetime.now(UTC),
    )
    assert doc.title_source == "auto"
