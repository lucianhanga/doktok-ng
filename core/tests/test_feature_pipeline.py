"""Activation records the feature ledger (ADR-0009), with in-memory repos (no DB)."""

from __future__ import annotations

from pathlib import Path

from doktok_contracts.schemas import FeatureStatus, JobStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.entities.extractor import RegexEntityExtractor
from doktok_core.entities.inmemory import InMemoryEntityRepository
from doktok_core.features.inmemory import InMemoryFeatureRepository
from doktok_core.ingestion.inmemory import InMemoryIngestionJobRepository
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices, process_file
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_modalities_files import DirectTextExtractor, PyMuPdfTextExtractor
from doktok_storage_filesystem import LocalFileStorage, QuarantineService, Sha256HashService

TENANT = "t1"


class FakeMime:
    def detect(self, path: str) -> str:  # noqa: ARG002
        return "text/plain"


def test_activation_records_done_features(tmp_path: Path) -> None:
    layout = FilesystemLayout(tmp_path, TENANT)
    layout.ensure()
    feature_repo = InMemoryFeatureRepository()
    services = IngestionServices(
        tenant_id=TENANT,
        job_repo=InMemoryIngestionJobRepository(),
        document_repo=InMemoryDocumentRepository(),
        file_storage=LocalFileStorage(),
        hash_service=Sha256HashService(),
        mime_detector=FakeMime(),
        security_policy=DefaultSecurityPolicy(max_file_mb=10),
        quarantine_service=QuarantineService(layout),
        text_extractor=DirectTextExtractor(),
        pdf_extractor=PyMuPdfTextExtractor(),
        layout=layout,
        entity_extractor=RegexEntityExtractor(),
        entity_repo=InMemoryEntityRepository(),
        feature_repo=feature_repo,
    )
    (layout.ingest / "note.txt").write_bytes(b"hello world for features")

    job = process_file(services, str(layout.ingest / "note.txt"))
    assert job.status is JobStatus.ACTIVE and job.document_id is not None

    by_name = {f.feature: f for f in feature_repo.list_for_document(TENANT, job.document_id)}
    assert by_name["extract"].status is FeatureStatus.DONE
    assert by_name["entities"].status is FeatureStatus.DONE  # entity_repo present
    assert "chunk_embed" not in by_name  # no chunk repo wired in this test
