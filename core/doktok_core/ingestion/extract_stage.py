"""The ``extract`` stage of the staged ingestion pipeline (ADR-0015).

Driven by the reconciler like any other stage: for a ``processing`` document it extracts the content
(born-digital text or OCR), writes the canonical artifacts, and flips the document to ``active`` via
``DocumentRepository.activate``. It does NOT index inline - ``chunk_embed``/``entities``/... run as
their own stages gated on ``extract`` being done. Failure is the reconciler's concern (retry/
backoff); a content duplicate discovered at activation is dropped (the winner already exists).
"""

from __future__ import annotations

import shutil
from collections.abc import Callable

from doktok_contracts.errors import DuplicateActiveDocumentError
from doktok_contracts.ports import DocumentRepository, FileStorage
from doktok_contracts.schemas import DocumentStatus

from doktok_core.documents.artifacts import write_document_artifacts
from doktok_core.entities.language import detect_language
from doktok_core.extraction.service import ExtractionResult
from doktok_core.ingestion.layout import FilesystemLayout

# (ExtractionResult, normalized_searchable_pdf | None) for a given (mime, source_path).
ContentExtractor = Callable[[str, str, str], tuple[ExtractionResult, bytes | None]]

_DETECTOR = "libmagic"  # records which MIME detector produced detected_mime (mirrors the pipeline)


class ExtractStage:
    """``extract`` stage: produce content + artifacts for a processing document and activate it."""

    name = "extract"
    version = 1
    dependencies: tuple[str, ...] = ()  # DAG root

    def __init__(
        self,
        document_repo: DocumentRepository,
        file_storage: FileStorage,
        files_root: str,
        extractor: ContentExtractor,
    ) -> None:
        self._documents = document_repo
        self._files = file_storage
        self._files_root = files_root  # the layout is tenant-scoped, so build it per call
        self._extract = extractor

    def process(self, tenant_id: str, document_id: str) -> None:
        document = self._documents.get(tenant_id, document_id)
        if document is None or document.status is not DocumentStatus.PROCESSING:
            return  # already activated, failed, or gone - nothing to do (idempotent)
        source = document.metadata.get("staged_source")
        if not source:
            raise ValueError(f"processing document {document_id} has no staged source path")

        layout = FilesystemLayout(self._files_root, tenant_id)
        result, normalized_pdf = self._extract(tenant_id, document.detected_mime or "", str(source))
        language = detect_language(result.content_md)
        artifacts = write_document_artifacts(
            self._files,
            layout,
            document_id,
            tenant_id=tenant_id,
            original_source_path=str(source),
            original_filename=document.original_filename,
            sha256=document.sha256,
            detected_mime=document.detected_mime,
            detector=_DETECTOR,
            result=result,
            normalized_pdf=normalized_pdf,
            language=language,
        )
        metadata: dict[str, object] = {
            "extraction_method": result.extraction_method,
            "page_count": result.page_count,
            "ocr_confidence": result.ocr_confidence,
            "language": language,
            "original": artifacts.original,
            "system_document": artifacts.system_document,
        }
        # The source mime when an office doc was converted to PDF (#313); absent otherwise.
        if result.normalized_from_mime:
            metadata["normalized_from"] = result.normalized_from_mime
        try:
            activated = self._documents.activate(
                tenant_id, document_id, storage_path=artifacts.storage_path, metadata=metadata
            )
        except DuplicateActiveDocumentError:
            # Another worker activated this content first: drop our artifacts + the duplicate row
            # (its ledger rows cascade away). The dependency gate never lets its features run.
            shutil.rmtree(artifacts.storage_path, ignore_errors=True)
            self._documents.delete(tenant_id, document_id)
            return
        if not activated:  # lost the race / no longer processing - don't leave orphan artifacts
            shutil.rmtree(artifacts.storage_path, ignore_errors=True)
