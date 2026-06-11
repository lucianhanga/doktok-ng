"""Pipeline entity-indexing test (M5) with in-memory repos (no DB)."""

from __future__ import annotations

from pathlib import Path

from doktok_contracts.media import ExtractedTerm
from doktok_contracts.schemas import EntityType, JobStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.entities.extractor import RegexEntityExtractor
from doktok_core.entities.inmemory import InMemoryEntityRepository
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


class FakeLexicalTermExtractor:
    """Stub that returns fixed lexemes (the real one uses PostgreSQL to_tsvector)."""

    def __init__(self, terms: list[ExtractedTerm]) -> None:
        self._terms = terms
        self.seen_config: str | None = None

    def extract_terms(
        self, text: str, *, config: str = "simple", limit: int = 200
    ) -> list[ExtractedTerm]:  # noqa: ARG002
        self.seen_config = config
        return self._terms


def test_entities_are_extracted_and_aggregated(tmp_path: Path) -> None:
    layout = FilesystemLayout(tmp_path, TENANT)
    layout.ensure()
    entity_repo = InMemoryEntityRepository()
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
        entity_repo=entity_repo,
    )
    body = b"Email a@b.com twice: a@b.com. Pay $50 by 2026-01-02."
    (layout.ingest / "note.txt").write_bytes(body)

    job = process_file(services, str(layout.ingest / "note.txt"))
    assert job.status is JobStatus.ACTIVE

    types = {e.entity_type for e in entity_repo.entities}
    assert EntityType.EMAIL in types
    assert EntityType.MONEY in types
    assert EntityType.DATE in types

    email = next(e for e in entity_repo.entities if e.entity_type is EntityType.EMAIL)
    assert email.normalized_value == "a@b.com"
    assert email.frequency == 2  # aggregated occurrences
    assert email.tenant_id == TENANT and email.document_id == job.document_id

    assert job.document_id is not None
    doc = services.document_repo.get(TENANT, job.document_id)
    assert doc is not None
    assert all(e.document_id == doc.id for e in entity_repo.entities)


def test_lexical_terms_stored_as_custom_tokens(tmp_path: Path) -> None:
    layout = FilesystemLayout(tmp_path, TENANT)
    layout.ensure()
    entity_repo = InMemoryEntityRepository()
    lexical = FakeLexicalTermExtractor([ExtractedTerm("invoic", 3), ExtractedTerm("payment", 1)])
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
        entity_repo=entity_repo,
        lexical_term_extractor=lexical,
    )
    body = b"This is clearly an English invoice document about a payment that is now due soon."
    (layout.ingest / "invoice.txt").write_bytes(body)

    job = process_file(services, str(layout.ingest / "invoice.txt"))
    assert job.status is JobStatus.ACTIVE

    tokens = {
        e.normalized_value for e in entity_repo.entities if e.entity_type is EntityType.CUSTOM_TOKEN
    }
    assert tokens == {"invoic", "payment"}
    invoic = next(e for e in entity_repo.entities if e.normalized_value == "invoic")
    assert invoic.frequency == 3 and invoic.metadata["language"] == "en"
    # English content -> the English non-stemming keyword config was requested.
    assert lexical.seen_config == "doktok_kw_english"

    assert job.document_id is not None
    doc = services.document_repo.get(TENANT, job.document_id)
    assert doc is not None and doc.metadata["language"] == "en"
