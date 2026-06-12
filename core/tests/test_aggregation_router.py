"""Chat aggregation router (M6.3 #158): question -> typed intent -> grounded aggregation answer."""

from __future__ import annotations

from datetime import UTC, date, datetime

from doktok_contracts.schemas import (
    AggregationIntent,
    Document,
    DocumentStatus,
    ExtractedRecord,
)
from doktok_core.aggregation import aggregation_answer, route_to_intent
from doktok_core.aggregation.inmemory import InMemoryRecordRepository
from doktok_core.documents.inmemory import InMemoryDocumentRepository


class FakeChat:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def test_semantic_question_is_not_routed_and_skips_the_llm() -> None:
    chat = FakeChat("{}")
    assert route_to_intent("what does my contract say about termination?", chat) is None
    assert chat.prompts == []  # no agg keywords -> the model is never even called


def test_aggregation_question_slot_fills_a_typed_intent() -> None:
    chat = FakeChat(
        'Here you go: {"is_aggregation": true, "operation": "sum", "merchant": "Block House", '
        '"direction": "debit", "currency": "EUR", "date_from": null, "date_to": null}'
    )
    intent = route_to_intent("how much did I spend at Block House?", chat)
    assert intent is not None
    assert intent.operation == "sum" and intent.merchant == "Block House"
    assert intent.direction == "debit" and intent.currency == "EUR"


def test_llm_says_not_aggregation_falls_back_to_rag() -> None:
    assert (
        route_to_intent("how many pages in total?", FakeChat('{"is_aggregation": false}')) is None
    )


def test_malformed_llm_reply_falls_back_to_rag() -> None:
    assert route_to_intent("total spend?", FakeChat("sorry, I cannot help")) is None


def test_aggregation_answer_formats_total_with_provenance() -> None:
    docs = InMemoryDocumentRepository()
    docs.add(
        Document(
            id="d1",
            tenant_id="t1",
            sha256="x",
            original_filename="stmt.pdf",
            title="Statement",
            status=DocumentStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
    )
    records = InMemoryRecordRepository()
    records.replace_for_document(
        "t1",
        "d1",
        [
            ExtractedRecord(
                id="r1",
                tenant_id="t1",
                document_id="d1",
                raw_text="BLOCK HOUSE 42.50",
                amount_minor=4250,
                currency="EUR",
                direction="debit",
                merchant_normalized="block house",
                occurred_on=date(2024, 2, 3),
            )
        ],
    )
    intent = AggregationIntent(operation="sum", merchant="block", direction="debit")
    result = records.aggregate("t1", intent)

    answer = aggregation_answer(intent, result, docs, "t1")
    assert "42.50" in answer.answer and "EUR" in answer.answer
    assert answer.grounded is True
    assert answer.citations[0].document_id == "d1"
    assert answer.citations[0].original_filename == "stmt.pdf"
