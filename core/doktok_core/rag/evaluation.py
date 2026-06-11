"""Deterministic RAG evaluation (M6.1).

Pure metric logic so it is CI-testable with fakes and reusable by the local runner (which drives it
against real Ollama models). Measures retrieval recall, grounded-answer correctness, citation
correctness, and refusal correctness over a golden set. Aggregation/enumeration cases are included
on purpose to *measure* the queries that top-k RAG cannot answer (the "beyond-RAG" gap).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from doktok_contracts.ports import RagAnswerer, Retriever


@dataclass
class RagCase:
    id: str
    question: str
    kind: str  # "factoid" | "aggregation" | "refusal" (informational, for per-kind reporting)
    expected_sources: list[str] = field(default_factory=list)  # filenames expected retrieved/cited
    expected_contains: list[str] = field(default_factory=list)  # substrings the answer must contain
    should_refuse: bool = False


@dataclass
class CaseResult:
    case: RagCase
    retrieved: bool  # an expected source appeared in the top-k retrieval
    grounded: bool
    answer: str
    answer_correct: bool  # all expected_contains present (case-insensitive)
    citation_correct: bool  # a citation maps to an expected source
    refusal_correct: bool  # should_refuse == (not grounded)

    @property
    def passed(self) -> bool:
        if self.case.should_refuse:
            return self.refusal_correct
        ok = self.grounded and self.answer_correct
        if self.case.expected_sources:
            ok = ok and self.citation_correct
        return ok


def _sources(filenames: Sequence[str | None]) -> set[str]:
    return {f for f in filenames if f}


def evaluate_case(
    case: RagCase, *, retriever: Retriever, answerer: RagAnswerer, tenant_id: str, k: int
) -> CaseResult:
    hits = retriever.search(tenant_id, case.question, k)
    retrieved_sources = _sources([h.original_filename or h.title for h in hits])
    retrieved = (
        bool(set(case.expected_sources) & retrieved_sources) if case.expected_sources else False
    )

    answer = answerer.answer(tenant_id, case.question, k)
    text = answer.answer.lower()
    answer_correct = all(s.lower() in text for s in case.expected_contains)
    cited_sources = _sources([c.original_filename or c.title for c in answer.citations])
    citation_correct = bool(set(case.expected_sources) & cited_sources)
    refusal_correct = case.should_refuse == (not answer.grounded)

    return CaseResult(
        case=case,
        retrieved=retrieved,
        grounded=answer.grounded,
        answer=answer.answer,
        answer_correct=answer_correct,
        citation_correct=citation_correct,
        refusal_correct=refusal_correct,
    )


@dataclass
class RagReport:
    results: list[CaseResult]

    def summary(self) -> dict[str, object]:
        answerable = [r for r in self.results if not r.case.should_refuse]
        refusals = [r for r in self.results if r.case.should_refuse]
        kinds = sorted({r.case.kind for r in self.results})
        return {
            "total": len(self.results),
            "passed": sum(1 for r in self.results if r.passed),
            "pass_rate": round(sum(1 for r in self.results if r.passed) / len(self.results), 4)
            if self.results
            else 0.0,
            "retrieval_recall": round(
                sum(1 for r in answerable if r.retrieved) / len(answerable), 4
            )
            if answerable
            else 0.0,
            "answer_accuracy": round(
                sum(1 for r in answerable if r.answer_correct) / len(answerable), 4
            )
            if answerable
            else 0.0,
            "citation_accuracy": round(
                sum(1 for r in answerable if r.citation_correct) / len(answerable), 4
            )
            if answerable
            else 0.0,
            "refusal_accuracy": round(
                sum(1 for r in refusals if r.refusal_correct) / len(refusals), 4
            )
            if refusals
            else 0.0,
            "by_kind": {
                kind: {
                    "total": sum(1 for r in self.results if r.case.kind == kind),
                    "passed": sum(1 for r in self.results if r.case.kind == kind and r.passed),
                }
                for kind in kinds
            },
        }


def evaluate(
    cases: Sequence[RagCase],
    *,
    retriever: Retriever,
    answerer: RagAnswerer,
    tenant_id: str,
    k: int = 8,
) -> RagReport:
    return RagReport(
        [
            evaluate_case(c, retriever=retriever, answerer=answerer, tenant_id=tenant_id, k=k)
            for c in cases
        ]
    )
