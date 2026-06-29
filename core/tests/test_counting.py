"""Deterministic document-count routing (ADR-0022 Phase 1). Pure functions + fakes, no DB/model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from doktok_contracts.ports import DocumentRepository, EntityRepository
from doktok_contracts.schemas import Document, DocumentStatus, EntityType
from doktok_core.aggregation.counting import (
    CountIntent,
    count_answer,
    count_documents,
    looks_like_count,
    parse_count_intent,
)


def _docs_repo(docs: FakeDocuments) -> DocumentRepository:
    return cast(DocumentRepository, docs)


def _entities_repo(entities: FakeEntities) -> EntityRepository:
    return cast(EntityRepository, entities)


class FakeDocuments:
    """Minimal DocumentRepository: title filter + a per-id store, enough for the count path."""

    def __init__(self, docs: dict[str, Document], title_index: dict[str, list[str]]) -> None:
        self._docs = docs
        self._title_index = title_index  # lowercased term -> matching ids (substring match)

    def list_document_ids(
        self,
        tenant_id: str,
        *,
        status: DocumentStatus | None = None,
        title: str | None = None,
        cap: int = 10_000,
        **_: object,
    ) -> tuple[list[str], int, bool]:
        if title is None:
            ids = list(self._docs)
        else:
            ids = [
                doc_id
                for term, lst in self._title_index.items()
                if title.lower() in term
                for doc_id in lst
            ]
            ids = sorted(set(ids))
        return ids[:cap], len(ids), len(ids) > cap

    def get(self, tenant_id: str, document_id: str) -> Document | None:
        return self._docs.get(document_id)


class FakeEntities:
    def __init__(self, mention_index: dict[str, list[str]]) -> None:
        self._mention_index = mention_index  # term substring -> doc ids

    def mention_document_ids(
        self,
        tenant_id: str,
        term: str,
        *,
        entity_type: EntityType | None = None,
        cap: int = 10_000,
    ) -> tuple[list[str], int, bool]:
        ids = sorted(
            {d for key, lst in self._mention_index.items() if term.lower() in key for d in lst}
        )
        return ids[:cap], len(ids), len(ids) > cap


def _doc(doc_id: str, title: str) -> Document:
    return Document(
        id=doc_id,
        tenant_id="t",
        sha256=doc_id,
        original_filename=f"{doc_id}.pdf",
        title=title,
        status=DocumentStatus.ACTIVE,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


# ---- gate + parsing ----


@pytest.mark.parametrize(
    "question,expected",
    [
        ("how many m-net invoices are in the system", True),
        ("how many invoices from m-net", True),
        ("number of documents do I have", True),
        ("list all contracts", True),
        ("how much did I spend at Block House", False),  # aggregation, no doc noun
        ("who is Stefan employed by", False),
        ("what is in this letter", False),  # no count verb
    ],
)
def test_looks_like_count(question: str, expected: bool) -> None:
    assert looks_like_count(question) is expected


def test_parse_count_intent_extracts_entity_and_doc_type() -> None:
    intent = parse_count_intent("how many m-net invoices are in the system")
    assert intent == CountIntent(entity="m-net", doc_type="invoice")


def test_parse_count_intent_whole_corpus() -> None:
    intent = parse_count_intent("how many documents are in the system")
    assert intent is not None and intent.entity is None and intent.doc_type == "document"


def test_parse_count_intent_none_for_non_count() -> None:
    assert parse_count_intent("how much did I spend at Aldi") is None


# ---- counting + answer ----


def test_count_documents_reports_title_and_mention_lenses() -> None:
    docs = FakeDocuments(
        {f"d{i}": _doc(f"d{i}", "M-net invoice") for i in range(3)},
        {"m-net invoice": ["d0", "d1", "d2"]},
    )
    entities = FakeEntities({"m-net": [f"d{i}" for i in range(10)]})
    report = count_documents(
        "t",
        CountIntent(entity="m-net", doc_type="invoice"),
        documents=_docs_repo(docs),
        entities=_entities_repo(entities),
    )
    counts = {lens.label: lens.count for lens in report.lenses}
    assert counts["with it in the title or name"] == 3
    assert counts["that mention it"] == 10
    assert report.note is not None  # doc-type-not-filterable caveat


def test_count_answer_is_grounded_and_labelled() -> None:
    docs = FakeDocuments({"d0": _doc("d0", "M-net invoice")}, {"m-net invoice": ["d0"]})
    entities = FakeEntities({"m-net": ["d0", "d1", "d2"]})
    report = count_documents(
        "t",
        CountIntent(entity="m-net", doc_type="invoice"),
        documents=_docs_repo(docs),
        entities=_entities_repo(entities),
    )
    answer = count_answer(report, _docs_repo(docs), "t")
    assert answer is not None and answer.grounded
    assert "m-net" in answer.answer
    assert "1" in answer.answer and "3" in answer.answer  # the two lens counts
    assert answer.citations  # at least the one resolvable sample doc
    assert answer.citations[0].document_id == "d0"


def test_count_answer_none_when_nothing_matches() -> None:
    docs = FakeDocuments({}, {})
    entities = FakeEntities({})
    report = count_documents(
        "t",
        CountIntent(entity="nonesuch", doc_type=None),
        documents=_docs_repo(docs),
        entities=_entities_repo(entities),
    )
    assert count_answer(report, _docs_repo(docs), "t") is None


def test_count_answer_truncated_says_at_least() -> None:
    docs = FakeDocuments({"d0": _doc("d0", "x")}, {})
    # A truncated lens (total exceeds the id cap) must read as "at least N", not an exact count.
    from doktok_core.aggregation.counting import CountLens, CountReport

    report = CountReport(
        entity="x",
        doc_type=None,
        lenses=[CountLens("that mention it", 12000, True, ["d0"])],
    )
    answer = count_answer(report, _docs_repo(docs), "t")
    assert answer is not None and "at least 12000" in answer.answer
