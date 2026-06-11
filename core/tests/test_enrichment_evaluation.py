"""Deterministic enrichment-eval scoring tests (no models)."""

from __future__ import annotations

from datetime import date

from doktok_core.enrichment.evaluation import (
    EnrichCase,
    EnrichReport,
    EnrichResult,
    evaluate_enrichment,
)


def _eval(
    case: EnrichCase,
    *,
    title: str | None = "Acme Invoice 2026",
    document_date: date | None = date(2026, 7, 1),
    location: str | None = "Berlin",
    summary: str | None = "An invoice.",
    categories: list[str] | None = None,
) -> EnrichResult:
    return evaluate_enrichment(
        case,
        title=title,
        document_date=document_date,
        location=location,
        summary=summary,
        categories=categories if categories is not None else ["Invoice", "Finance"],
    )


def test_all_checks_pass() -> None:
    case = EnrichCase(
        file="invoice.txt",
        title_contains=["invoice"],
        expect_date="2026-07-01",
        location_contains="Berlin",
        categories_any=["financ"],
    )
    assert _eval(case).passed is True


def test_title_equal_to_filename_stem_fails() -> None:
    result = _eval(EnrichCase(file="invoice.txt"), title="invoice")  # bare stem
    assert result.title_ok is False and result.passed is False


def test_wrong_date_and_na_handling() -> None:
    assert _eval(EnrichCase(file="x.txt", expect_date="2026-01-01")).date_ok is False
    assert _eval(EnrichCase(file="x.txt", expect_date="n/a"), document_date=None).date_ok is True
    assert (
        _eval(EnrichCase(file="x.txt", location_contains="n/a"), location=None).location_ok is True
    )


def test_category_must_match_one() -> None:
    assert _eval(EnrichCase(file="x.txt", categories_any=["legal"])).categories_ok is False
    assert _eval(EnrichCase(file="x.txt", categories_any=["invoice"])).categories_ok is True


def test_summary_required() -> None:
    assert _eval(EnrichCase(file="x.txt"), summary="").summary_ok is False


def test_report_summary_aggregates() -> None:
    good = _eval(EnrichCase(file="invoice.txt", categories_any=["invoice"]))
    bad = _eval(EnrichCase(file="x.txt"), title="x", summary="")
    report = EnrichReport([good, bad])
    s = report.summary()
    assert s["total"] == 2 and s["passed"] == 1
    assert s["summary_accuracy"] == 0.5
