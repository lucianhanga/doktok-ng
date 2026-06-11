"""Deterministic scoring for document-enrichment quality (M6.2 eval).

Pure metric logic so it is CI-testable with fakes and reusable by the local runner (which drives
the real enrichment features against Ollama). Measures title sanity, document-date correctness,
location, category relevance, and summary presence against a golden set.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date


@dataclass
class EnrichCase:
    file: str
    title_contains: list[str] = field(default_factory=list)  # any of these (case-insensitive)
    expect_date: str | None = None  # "YYYY-MM-DD" or "n/a"; None = don't check
    location_contains: str | None = None  # "n/a" means expect no location
    categories_any: list[str] = field(default_factory=list)  # at least one must match
    summary_required: bool = True


@dataclass
class EnrichResult:
    case: EnrichCase
    title_ok: bool
    date_ok: bool
    location_ok: bool
    categories_ok: bool
    summary_ok: bool

    @property
    def passed(self) -> bool:
        return all(
            [self.title_ok, self.date_ok, self.location_ok, self.categories_ok, self.summary_ok]
        )


def _contains_any(text: str | None, needles: Sequence[str]) -> bool:
    haystack = (text or "").lower()
    return any(n.lower() in haystack for n in needles)


def evaluate_enrichment(
    case: EnrichCase,
    *,
    title: str | None,
    document_date: date | None,
    location: str | None,
    summary: str | None,
    categories: Sequence[str],
) -> EnrichResult:
    # Title: non-empty, not the bare filename stem, and contains an expected keyword if given.
    stem = case.file.rsplit(".", 1)[0].lower()
    title_ok = bool(title) and (title or "").strip().lower() != stem
    if case.title_contains:
        title_ok = title_ok and _contains_any(title, case.title_contains)

    if case.expect_date is None:
        date_ok = True
    elif case.expect_date == "n/a":
        date_ok = document_date is None
    else:
        date_ok = document_date is not None and document_date.isoformat() == case.expect_date

    if case.location_contains is None:
        location_ok = True
    elif case.location_contains == "n/a":
        location_ok = location is None
    else:
        location_ok = _contains_any(location, [case.location_contains])

    categories_ok = (not case.categories_any) or any(
        _contains_any(c, case.categories_any) for c in categories
    )
    summary_ok = (not case.summary_required) or bool(summary and summary.strip())

    return EnrichResult(
        case=case,
        title_ok=title_ok,
        date_ok=date_ok,
        location_ok=location_ok,
        categories_ok=categories_ok,
        summary_ok=summary_ok,
    )


@dataclass
class EnrichReport:
    results: list[EnrichResult]

    def summary(self) -> dict[str, object]:
        n = len(self.results)
        return {
            "total": n,
            "passed": sum(1 for r in self.results if r.passed),
            "title_accuracy": round(sum(r.title_ok for r in self.results) / n, 4) if n else 0.0,
            "date_accuracy": round(sum(r.date_ok for r in self.results) / n, 4) if n else 0.0,
            "location_accuracy": round(sum(r.location_ok for r in self.results) / n, 4)
            if n
            else 0.0,
            "category_accuracy": round(sum(r.categories_ok for r in self.results) / n, 4)
            if n
            else 0.0,
            "summary_accuracy": round(sum(r.summary_ok for r in self.results) / n, 4) if n else 0.0,
        }
