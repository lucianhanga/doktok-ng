"""Deterministic RAG-evaluation metric tests (fakes; no models)."""

from __future__ import annotations

from doktok_contracts.schemas import Citation, RagAnswer, SearchHit
from doktok_core.rag.evaluation import RagCase, evaluate


class FakeRetriever:
    def __init__(self, by_question: dict[str, list[str]]) -> None:
        self._by_question = by_question  # question -> list of source filenames retrieved

    def search(self, tenant_id, query, limit=10, *, filters=None):  # type: ignore[no-untyped-def]  # noqa: ARG002
        return [
            SearchHit(
                document_id=fn, chunk_id=f"{fn}-c", original_filename=fn, snippet="", score=1.0
            )
            for fn in self._by_question.get(query, [])
        ]


class FakeAnswerer:
    def __init__(
        self, by_question: dict[str, RagAnswer], *, rewrites: dict[str, str] | None = None
    ) -> None:
        self._by_question = by_question
        self._rewrites = rewrites or {}
        self.threaded: list[
            tuple[int, str]
        ] = []  # (history length, question) seen via answer_thread

    def answer(self, tenant_id: str, question: str, limit: int = 8) -> RagAnswer:  # noqa: ARG002
        return self._by_question.get(question, RagAnswer(answer="idk", grounded=False))

    def answer_thread(self, tenant_id, history, question, limit=8):  # type: ignore[no-untyped-def]
        self.threaded.append((len(history), question))
        # A follow-up resolves to a standalone query; answer that, reporting the rewrite.
        standalone = self._rewrites.get(question, question)
        answer = self.answer(tenant_id, standalone, limit)
        return answer.model_copy(
            update={"rewritten_query": standalone if standalone != question else None}
        )

    def answer_thread_stream(self, tenant_id, history, question, limit=8, *, reasoning=None):  # type: ignore[no-untyped-def]
        yield from ()  # not exercised by the eval harness


def _answer(text: str, sources: list[str]) -> RagAnswer:
    return RagAnswer(
        answer=text,
        grounded=True,
        citations=[
            Citation(index=i, document_id=s, chunk_id=f"{s}-c", original_filename=s, snippet="")
            for i, s in enumerate(sources, start=1)
        ],
    )


REFUSAL = "I could not find enough evidence in the indexed documents to answer that."


def _cases() -> list[RagCase]:
    return [
        RagCase("good", "Q1", "factoid", expected_sources=["a.txt"], expected_contains=["42"]),
        RagCase("refuse", "Q2", "refusal", should_refuse=True),
        RagCase("agg", "Q3", "aggregation", expected_sources=["s1.txt"], expected_contains=["75"]),
    ]


def test_scores_pass_fail_and_aggregates() -> None:
    retriever = FakeRetriever({"Q1": ["a.txt"], "Q2": [], "Q3": ["s1.txt"]})
    answerer = FakeAnswerer(
        {
            "Q1": _answer("The answer is 42 [1].", ["a.txt"]),  # passes
            "Q2": RagAnswer(answer=REFUSAL, grounded=False),  # refusal correct
            "Q3": _answer("I think it was around fifty euros [1].", ["s1.txt"]),  # wrong total
        }
    )
    report = evaluate(_cases(), retriever=retriever, answerer=answerer, tenant_id="t1")
    by_id = {r.case.id: r for r in report.results}

    assert by_id["good"].passed is True
    assert by_id["refuse"].passed is True and by_id["refuse"].refusal_correct is True
    assert by_id["agg"].passed is False  # the aggregation gap is measured, not hidden
    assert by_id["agg"].retrieved is True and by_id["agg"].answer_correct is False

    summary = report.summary()
    assert summary["total"] == 3 and summary["passed"] == 2
    assert summary["refusal_accuracy"] == 1.0
    assert summary["retrieval_recall"] == 1.0  # both answerable cases retrieved their source
    by_kind = summary["by_kind"]
    assert isinstance(by_kind, dict)
    assert by_kind["aggregation"] == {"total": 1, "passed": 0}


def test_conversation_case_uses_answer_thread_and_rewrite_for_retrieval() -> None:
    # The bare follow-up ("Who is it billed to?") retrieves nothing; the rewrite does. The eval must
    # go through answer_thread and measure recall against the rewritten query.
    retriever = FakeRetriever(
        {"Who issued invoice INV-1?": ["invoice.txt"], "Who is it billed to?": []}
    )
    answerer = FakeAnswerer(
        {"Who issued invoice INV-1?": _answer("Billed to Acme [1].", ["invoice.txt"])},
        rewrites={"Who is it billed to?": "Who issued invoice INV-1?"},
    )
    case = RagCase(
        "followup",
        "Who is it billed to?",
        "conversation",
        expected_sources=["invoice.txt"],
        expected_contains=["Acme"],
        history=[
            {"role": "user", "content": "What is the total on invoice INV-1?"},
            {"role": "assistant", "content": "199 EUR [1]."},
        ],
    )
    report = evaluate([case], retriever=retriever, answerer=answerer, tenant_id="t1")

    assert answerer.threaded == [(2, "Who is it billed to?")]  # routed through answer_thread
    result = report.results[0]
    assert result.passed is True  # rewrite -> right source retrieved + cited + text matches
    assert result.retrieved is True and result.citation_correct is True


def test_missing_citation_fails_even_if_text_matches() -> None:
    retriever = FakeRetriever({"Q1": ["a.txt"]})
    answerer = FakeAnswerer({"Q1": _answer("The answer is 42.", [])})  # no citations
    report = evaluate(
        [RagCase("good", "Q1", "factoid", expected_sources=["a.txt"], expected_contains=["42"])],
        retriever=retriever,
        answerer=answerer,
        tenant_id="t1",
    )
    assert report.results[0].passed is False
    assert report.results[0].citation_correct is False
