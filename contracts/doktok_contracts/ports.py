"""Core ports (interfaces) for DokTok NG.

Core domain logic depends on these Protocols, never on concrete adapters (ADR-0001). Adapters in
providers/, storage/, modalities/, and retrieval/ implement them. For M0 these are interface
declarations only; methods are intentionally unimplemented.

See brief section 9 for the full list.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from doktok_contracts.media import OcrPageResult, RenderedPage
from doktok_contracts.schemas import (
    AuditEvent,
    Document,
    DocumentArtifact,
    DocumentChunk,
    DocumentEntity,
    DocumentVersion,
    IngestionJob,
    SecurityDecision,
)

# --- Repositories ---------------------------------------------------------------------------


@runtime_checkable
class DocumentRepository(Protocol):
    def get(self, tenant_id: str, document_id: str) -> Document | None: ...
    def add(self, document: Document) -> None: ...
    def list_documents(
        self, tenant_id: str, limit: int = 50, offset: int = 0
    ) -> list[Document]: ...


@runtime_checkable
class DocumentVersionRepository(Protocol):
    def get(self, version_id: str) -> DocumentVersion | None: ...
    def add(self, version: DocumentVersion) -> None: ...


@runtime_checkable
class IngestionJobRepository(Protocol):
    def get(self, tenant_id: str, job_id: str) -> IngestionJob | None: ...
    def add(self, job: IngestionJob) -> None: ...
    def update(self, job: IngestionJob) -> None: ...
    def list_jobs(self, tenant_id: str, limit: int = 50, offset: int = 0) -> list[IngestionJob]: ...
    def find_by_sha256(self, tenant_id: str, sha256: str) -> list[IngestionJob]: ...


@runtime_checkable
class DocumentArtifactRepository(Protocol):
    def add(self, artifact: DocumentArtifact) -> None: ...
    def list_for_document(self, document_id: str) -> list[DocumentArtifact]: ...


@runtime_checkable
class AuditLogRepository(Protocol):
    """Append-only activity/audit trail (record + read only; events are immutable)."""

    def record(self, event: AuditEvent) -> None: ...
    def list_events(
        self,
        tenant_id: str,
        *,
        document_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]: ...


# --- File / IO ------------------------------------------------------------------------------


@runtime_checkable
class FileStorage(Protocol):
    def move(self, source: str, destination: str) -> None: ...
    def read_bytes(self, path: str) -> bytes: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    def write_text(self, path: str, text: str) -> None: ...


@runtime_checkable
class MimeDetector(Protocol):
    def detect(self, path: str) -> str: ...


@runtime_checkable
class HashService(Protocol):
    def sha256(self, path: str) -> str: ...


# --- Extraction -----------------------------------------------------------------------------


@runtime_checkable
class TextExtractor(Protocol):
    def extract(self, path: str) -> str: ...


@runtime_checkable
class PdfClassifier(Protocol):
    def page_image_coverage(self, path: str) -> list[float]:
        """Per page, the fraction (0-1) of the page area covered by its largest embedded image."""
        ...


@runtime_checkable
class PdfTextExtractor(Protocol):
    def extract_pages(self, path: str) -> list[str]: ...


@runtime_checkable
class OcrExtractor(Protocol):
    def ocr_image(self, image_png: bytes) -> OcrPageResult: ...


@runtime_checkable
class PdfRenderer(Protocol):
    def render_pages(self, path: str, dpi: int = 200) -> list[bytes]: ...


@runtime_checkable
class SearchablePdfBuilder(Protocol):
    def build(self, pages: list[RenderedPage]) -> bytes: ...


@runtime_checkable
class ImageExtractor(Protocol):
    def extract(self, path: str) -> str: ...


@runtime_checkable
class MarkdownExtractor(Protocol):
    def extract(self, path: str) -> str: ...


# --- Indexing / AI --------------------------------------------------------------------------


@runtime_checkable
class Chunker(Protocol):
    def chunk(self, text: str) -> list[DocumentChunk]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class ChatModelProvider(Protocol):
    def complete(self, prompt: str) -> str: ...


@runtime_checkable
class EntityExtractor(Protocol):
    def extract(self, text: str) -> list[DocumentEntity]: ...


@runtime_checkable
class Retriever(Protocol):
    def search(self, query: str, limit: int = 10) -> list[DocumentChunk]: ...


@runtime_checkable
class RagAnswerer(Protocol):
    def answer(self, question: str) -> str: ...


# --- Security -------------------------------------------------------------------------------


@runtime_checkable
class SecurityPolicy(Protocol):
    def is_allowed(self, mime: str, size_bytes: int) -> bool: ...
    def decide(self, mime: str, size_bytes: int) -> SecurityDecision: ...


@runtime_checkable
class QuarantineService(Protocol):
    def quarantine(self, path: str, reason: str) -> None: ...
