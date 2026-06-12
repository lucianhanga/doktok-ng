"""Staged ingestion intake (ADR-0015): create a processing document + seed the stage ledger."""

from __future__ import annotations

from pathlib import Path

from doktok_contracts.schemas import DocumentStatus, FeatureStatus, JobStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.features.inmemory import InMemoryFeatureRepository
from doktok_core.ingestion.inmemory import InMemoryIngestionJobRepository
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices, process_file
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_modalities_files import DirectTextExtractor, PyMuPdfTextExtractor
from doktok_storage_filesystem import LocalFileStorage, QuarantineService, Sha256HashService

TENANT = "t1"
STAGES = [
    ("extract", 1),
    ("chunk_embed", 2),
    ("entities", 3),
    ("doc_metadata", 1),
    ("thumbnail", 1),
]


class FakeMime:
    def detect(self, path: str) -> str:  # noqa: ARG002
        return "text/plain"


def test_staged_intake_creates_processing_document_and_seeds_the_ledger(tmp_path: Path) -> None:
    layout = FilesystemLayout(tmp_path, TENANT)
    layout.ensure()
    docs = InMemoryDocumentRepository()
    features = InMemoryFeatureRepository()
    services = IngestionServices(
        tenant_id=TENANT,
        job_repo=InMemoryIngestionJobRepository(),
        document_repo=docs,
        file_storage=LocalFileStorage(),
        hash_service=Sha256HashService(),
        mime_detector=FakeMime(),
        security_policy=DefaultSecurityPolicy(max_file_mb=10),
        quarantine_service=QuarantineService(layout),
        text_extractor=DirectTextExtractor(),
        pdf_extractor=PyMuPdfTextExtractor(),
        layout=layout,
        feature_repo=features,
        staged_ingestion=True,
        stage_ledger=STAGES,
    )
    (layout.ingest / "note.txt").write_bytes(b"hello staged world")

    job = process_file(services, str(layout.ingest / "note.txt"))

    # Intake handed off: the job is done and a `processing` document exists (hidden from the
    # library), but extraction has NOT run yet.
    assert job.status is JobStatus.ACTIVE and job.document_id is not None
    doc = docs.get(TENANT, job.document_id)
    assert doc is not None
    assert doc.status is DocumentStatus.PROCESSING
    assert doc.storage_path is None  # no artifacts yet
    assert doc.metadata["staged_source"]  # the workdir original the extract stage will read

    # Every stage is seeded pending (the dependency gate keeps all but extract waiting).
    seeded = {f.feature: f.status for f in features.list_for_document(TENANT, job.document_id)}
    assert seeded == {name: FeatureStatus.PENDING for name, _ in STAGES}
