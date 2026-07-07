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


def test_caps_at_eight_per_document() -> None:
    cats = InMemoryCategoryRepository()
    names = _run(["a", "b", "c", "d", "e", "f", "g", "h", "i"], cats)
    assert len(names) == 8


def test_at_tenant_cap_force_picks_nearest_existing() -> None:
    cats = InMemoryCategoryRepository()
    for i in range(50):  # fill the 50-category vocabulary
        cats.create("t1", f"category{i:02d}", f"category{i:02d}")
    assert cats.active_count("t1") == 50
    names = _run(["category00 variant"], cats)  # nothing new can be created
    assert cats.active_count("t1") == 50  # still capped
    assert names == ["category00"]  # mapped to the nearest existing


def test_find_similar_below_new_threshold_creates_new_category() -> None:
    """A ~0.60-similar label creates a new category, not a merge, under the 0.70 threshold.

    "invoicd" shares 6 of 10 union trigrams with "invoice" -> Jaccard = 0.60.
    The old default threshold (0.55) would merge these; the new one (0.70) does not.
    """
    cats = InMemoryCategoryRepository()
    cats.create("t1", "Invoice", "invoice")
    assert cats.find_similar("t1", "invoicd") is None  # 0.60 < 0.70 -> no merge
    assert cats.find_similar("t1", "invoicd", threshold=0.55) is not None  # 0.60 >= 0.55


def test_set_document_categories_stores_rank_in_list_order() -> None:
    """set_document_categories preserves insertion order as rank."""
    cats = InMemoryCategoryRepository()
    finance = cats.create("t1", "Finance", "finance")
    internal = cats.create("t1", "Internal Communication", "internal communication")
    assert finance and internal

    # Finance passed first -> rank 0 (primary), Internal Communication -> rank 1.
    cats.set_document_categories("t1", "d1", [finance.id, internal.id])
    assert cats.primary_categories("t1", ["d1"]) == {"d1": "Finance"}

    # Overwrite with reversed order -> Internal Communication becomes rank 0.
    cats.set_document_categories("t1", "d1", [internal.id, finance.id])
    assert cats.primary_categories("t1", ["d1"]) == {"d1": "Internal Communication"}


def test_primary_categories_returns_rank_zero_not_globally_most_common() -> None:
    """primary_categories must respect each doc's rank-0 label, not the tenant-wide count."""
    cats = InMemoryCategoryRepository()
    finance = cats.create("t1", "Finance", "finance")
    internal = cats.create("t1", "Internal Communication", "internal communication")
    assert finance and internal

    # d1: rank-0 = Finance (minority label across the tenant)
    # d2, d3, d4: rank-0 = Internal Communication (globally most common)
    cats.set_document_categories("t1", "d1", [finance.id, internal.id])
    cats.set_document_categories("t1", "d2", [internal.id])
    cats.set_document_categories("t1", "d3", [internal.id])
    cats.set_document_categories("t1", "d4", [internal.id])

    primary = cats.primary_categories("t1", ["d1", "d2"])
    # d1's primary must be Finance even though Internal Communication is globally more common.
    assert primary["d1"] == "Finance"
    assert primary["d2"] == "Internal Communication"
