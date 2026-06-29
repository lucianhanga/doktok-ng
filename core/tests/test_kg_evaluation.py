"""Deterministic knowledge-graph quality metrics (KAG graph-quality tracks): edge precision/recall
and provenance correctness. Pure functions, no DB and no model."""

from __future__ import annotations

from doktok_core.knowledge_graph.evaluation import (
    EdgeTriple,
    ProvenanceInput,
    check_provenance,
    evaluate_provenance,
    score_edges,
)

# ----------------------------------------------------------------------- edge precision/recall


def _gold() -> list[EdgeTriple]:
    return [
        EdgeTriple("Johanna Mertens", "INSURED_BY", "Allianz Versicherung AG", "insurance.txt"),
        EdgeTriple("Stefan Vogel", "BANKS_WITH", "Deutsche Bank", "bank.txt"),
        EdgeTriple("Stefan Vogel", "EMPLOYED_BY", "Siemens AG", "employment.txt"),
    ]


def test_perfect_match_after_normalization() -> None:
    # Different casing/whitespace + lower-cased predicate still match (normalize_ner_name + upper).
    extracted = [
        EdgeTriple("johanna   mertens", "insured_by", "Allianz Versicherung AG"),
        EdgeTriple("Stefan Vogel", "BANKS_WITH", "deutsche bank"),
        EdgeTriple("STEFAN VOGEL", "employed_by", "Siemens AG"),
    ]
    report = score_edges(extracted, _gold())
    assert report.overall.precision == 1.0
    assert report.overall.recall == 1.0
    assert report.overall.f1 == 1.0
    assert report.missed_gold == [] and report.spurious == []


def test_missed_gold_lowers_recall() -> None:
    # Only 2 of 3 gold edges extracted -> recall 2/3, precision 1.0.
    extracted = [
        EdgeTriple("Johanna Mertens", "INSURED_BY", "Allianz Versicherung AG"),
        EdgeTriple("Stefan Vogel", "BANKS_WITH", "Deutsche Bank"),
    ]
    report = score_edges(extracted, _gold())
    assert report.overall.recall == round(2 / 3, 4)
    assert report.overall.precision == 1.0
    assert len(report.missed_gold) == 1
    assert report.missed_gold[0].predicate == "EMPLOYED_BY"


def test_spurious_edge_lowers_precision() -> None:
    # An extra (wrong) edge not in gold -> precision 3/4, recall 1.0.
    extracted = [
        *[EdgeTriple(g.subject, g.predicate, g.object) for g in _gold()],
        EdgeTriple("Stefan Vogel", "EMPLOYED_BY", "Allianz Versicherung AG"),  # hallucinated
    ]
    report = score_edges(extracted, _gold())
    assert report.overall.recall == 1.0
    assert report.overall.precision == round(3 / 4, 4)
    assert len(report.spurious) == 1
    assert report.spurious[0].object == "Allianz Versicherung AG"


def test_per_predicate_breakdown() -> None:
    extracted = [
        EdgeTriple("Johanna Mertens", "INSURED_BY", "Allianz Versicherung AG"),  # correct
        EdgeTriple("Stefan Vogel", "BANKS_WITH", "Commerzbank"),  # wrong object
        # EMPLOYED_BY missing entirely
    ]
    report = score_edges(extracted, _gold())
    by_pred = report.per_predicate
    assert by_pred["INSURED_BY"].precision == 1.0 and by_pred["INSURED_BY"].recall == 1.0
    # BANKS_WITH: gold=Deutsche Bank edge, extracted=Commerzbank edge -> 0 TP, P/R both 0
    assert by_pred["BANKS_WITH"].true_positives == 0
    assert by_pred["BANKS_WITH"].precision == 0.0 and by_pred["BANKS_WITH"].recall == 0.0
    # EMPLOYED_BY: gold-only, never extracted -> recall 0, precision 0 (no extracted of that pred)
    assert by_pred["EMPLOYED_BY"].recall == 0.0
    assert by_pred["EMPLOYED_BY"].extracted_total == 0


def test_empty_extraction_is_zero_not_crash() -> None:
    report = score_edges([], _gold())
    assert report.overall.precision == 0.0
    assert report.overall.recall == 0.0
    assert report.overall.f1 == 0.0
    assert len(report.missed_gold) == 3


# ----------------------------------------------------------------------- provenance correctness

_DOC = (
    "Welcome to Deutsche Bank. Stefan Vogel banks with Deutsche Bank as of this month. "
    "Account holder: Stefan Vogel."
)
_EDGE = EdgeTriple("Stefan Vogel", "BANKS_WITH", "Deutsche Bank", "bank.txt")


def test_provenance_valid_for_good_evidence() -> None:
    check = check_provenance(
        ProvenanceInput(_EDGE, "bank.txt", "Stefan Vogel banks with Deutsche Bank", _DOC)
    )
    assert check.valid is True and check.reason == ""


def test_provenance_invalid_empty_evidence() -> None:
    check = check_provenance(ProvenanceInput(_EDGE, "bank.txt", "   ", _DOC))
    assert check.valid is False and check.reason == "empty evidence"


def test_provenance_invalid_evidence_not_in_document() -> None:
    # A plausible-sounding but fabricated span that is not in the source text.
    check = check_provenance(
        ProvenanceInput(_EDGE, "bank.txt", "Stefan Vogel banks with Commerzbank", _DOC)
    )
    assert check.valid is False
    assert check.reason == "evidence not found in source document"


def test_provenance_invalid_when_endpoint_missing_from_span() -> None:
    # The span is in the document but does not mention the object endpoint.
    check = check_provenance(
        ProvenanceInput(_EDGE, "bank.txt", "Account holder: Stefan Vogel.", _DOC)
    )
    assert check.valid is False
    assert "Deutsche Bank" in check.reason


def test_evaluate_provenance_rate_and_offenders() -> None:
    items = [
        ProvenanceInput(_EDGE, "bank.txt", "Stefan Vogel banks with Deutsche Bank", _DOC),  # valid
        ProvenanceInput(_EDGE, "bank.txt", "", _DOC),  # empty
    ]
    report = evaluate_provenance(items)
    assert report.total == 2 and report.valid == 1
    assert report.rate == 0.5
    assert len(report.invalid) == 1 and report.invalid[0].reason == "empty evidence"
