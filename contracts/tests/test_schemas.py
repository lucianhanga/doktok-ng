from datetime import UTC, datetime

from doktok_contracts.schemas import (
    Document,
    DocumentStatus,
    IngestionJob,
    JobStatus,
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
    }
    assert {s.value for s in JobStatus} == expected
