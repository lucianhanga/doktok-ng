"""Unidentifiable detection (ADR-0017, M7.3 Phase 2): the detector + the feature behaviours."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.media import ExtractedMetadata
from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.categories.inmemory import InMemoryCategoryRepository
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.enrichment import detect_unidentifiable
from doktok_core.features.processors import DocClassifyFeature, DocMetadataFeature

GARBAGE = "60170974C516 8829 //// #### 0000 ;;; %%% 12 34 56 78 90 :: == 4452 7781 0091 33"
PROSE = "This annual report summarises Acme's financial results for the 2026 fiscal year in detail."


class FakeFiles:
    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    def read_bytes(self, path: str) -> bytes:
        try:
            return self._files[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    def move(self, source: str, destination: str) -> None: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    def write_text(self, path: str, text: str) -> None: ...


class FakeExtractor:
    def extract(self, text: str) -> ExtractedMetadata:  # noqa: ARG002
        return ExtractedMetadata("Unidentifiable Document", None, None, "unreadable")


class FakeClassifier:
    def classify(self, text: str, existing: list[str]) -> list[str]:  # noqa: ARG002
        return ["Invoices", "Contracts"]


def _doc() -> Document:
    return Document(
        id="d1",
        tenant_id="t1",
        sha256="x",
        original_filename="scan.jpg",
        status=DocumentStatus.ACTIVE,
        storage_path="/store/d1",
        created_at=datetime.now(UTC),
    )


def test_detector_flags_garbage_keeps_prose_and_abstains_on_tiny() -> None:
    assert detect_unidentifiable(PROSE) is False
    assert detect_unidentifiable(GARBAGE) is True
    assert detect_unidentifiable("too short") is None  # < 40 chars -> unassessed


def test_doc_metadata_sets_the_unidentifiable_flag() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(_doc())
    DocMetadataFeature(
        repo, FakeFiles({"/store/d1/content.md": GARBAGE.encode()}), lambda _t: FakeExtractor()
    ).process("t1", "d1")
    assert repo.get("t1", "d1").unidentifiable is True  # type: ignore[union-attr]


def test_doc_classify_suppresses_and_clears_categories_for_unidentifiable() -> None:
    repo = InMemoryDocumentRepository()
    doc = _doc()
    doc.unidentifiable = True
    repo.add(doc)
    categories = InMemoryCategoryRepository()
    inv = categories.create("t1", "Invoices", "invoices")
    assert inv
    categories.set_document_categories("t1", "d1", [inv.id])  # a spurious pre-existing category

    DocClassifyFeature(
        repo,
        FakeFiles({"/store/d1/content.md": GARBAGE.encode()}),
        lambda _t: FakeClassifier(),
        categories,
    ).process("t1", "d1")

    # Flagged -> no categories assigned, and the pre-existing one is cleared.
    assert categories.list_for_document("t1", "d1") == []


def test_doc_classify_still_classifies_identifiable_docs() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(_doc())  # unidentifiable is None
    categories = InMemoryCategoryRepository()
    DocClassifyFeature(
        repo,
        FakeFiles({"/store/d1/content.md": PROSE.encode()}),
        lambda _t: FakeClassifier(),
        categories,
    ).process("t1", "d1")
    assert {c.name for c in categories.list_for_document("t1", "d1")} == {"Invoices", "Contracts"}
