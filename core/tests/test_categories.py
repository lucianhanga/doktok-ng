"""Category normalization + the DocClassifyFeature resolve/cap behavior (M6.2)."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.categories import InMemoryCategoryRepository
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.enrichment import normalize_category
from doktok_core.features.processors import DocClassifyFeature


def test_normalize_dedupes_plurals_and_case() -> None:
    assert normalize_category("Invoices") == normalize_category("invoice") == "invoice"
    assert normalize_category("  Legal Docs! ") == "legal doc"
    assert normalize_category("Business") == "business"  # 'ss' not de-pluralized


class FakeFileStorage:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def read_bytes(self, path: str) -> bytes:
        return self._content

    def move(self, source: str, destination: str) -> None: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    def write_text(self, path: str, text: str) -> None: ...


class FakeClassifier:
    def __init__(self, labels: list[str]) -> None:
        self._labels = labels

    def classify(self, text: str, existing: list[str]) -> list[str]:
        return self._labels


def _doc(doc_id: str = "d1") -> Document:
    return Document(
        id=doc_id,
        tenant_id="t1",
        sha256="x",
        original_filename="f.pdf",
        status=DocumentStatus.ACTIVE,
        storage_path=f"/store/{doc_id}",
        created_at=datetime.now(UTC),
    )


def _run(labels: list[str], cats: InMemoryCategoryRepository, doc_id: str = "d1") -> list[str]:
    docs = InMemoryDocumentRepository()
    docs.add(_doc(doc_id))
    files = FakeFileStorage(b"some document content")
    DocClassifyFeature(docs, files, FakeClassifier(labels), cats).process("t1", doc_id)
    return [c.name for c in cats.list_for_document("t1", doc_id)]


def test_creates_and_reuses_categories() -> None:
    cats = InMemoryCategoryRepository()
    assert sorted(_run(["Invoice", "Finance"], cats)) == ["Finance", "Invoice"]
    # second doc reuses the same vocabulary (plural folds to the existing "Invoice")
    names = _run(["Invoices"], cats, "d2")
    assert names == ["Invoice"]
    assert cats.active_count("t1") == 2  # no new category created


def test_caps_at_five_per_document() -> None:
    cats = InMemoryCategoryRepository()
    names = _run(["a", "b", "c", "d", "e", "f", "g"], cats)
    assert len(names) == 5


def test_at_tenant_cap_force_picks_nearest_existing() -> None:
    cats = InMemoryCategoryRepository()
    for i in range(20):  # fill the 20-category vocabulary
        cats.create("t1", f"category{i:02d}", f"category{i:02d}")
    assert cats.active_count("t1") == 20
    names = _run(["category00 variant"], cats)  # nothing new can be created
    assert cats.active_count("t1") == 20  # still capped
    assert names == ["category00"]  # mapped to the nearest existing
