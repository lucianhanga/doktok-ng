"""JOB_TITLE evaluation harness over the REAL GLiNER model (#518 Phase 2).

A small labeled multilingual fixture (German + English): positive cases each contain exactly one
job title the extractor must find; negative cases contain person names, organisations, places and
common nouns on which emitting ANY job title is a false positive. The harness reports both sides
of the model's behaviour, mirroring the Phase 1 precision/recall gates for the validated types:

- precision gate: the false-positive rate over the negatives (target: 0.0 - the JOB_TITLE
  confidence threshold exists precisely to keep this at zero);
- recall report: the false-negative rate over the positives. Model output is open-class, so the
  gate is a conservative floor rather than 1.0; the assertion message lists every miss so recall
  regressions are visible, not silent.

Needs the ``gliner`` engine extra (torch + model download) and skips cleanly when it is not
installed - same pattern as the libpostal-gated address tests. The deterministic stub tests in
``test_gliner_adapter.py`` always run and cover the mapping/threshold wiring.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

import pytest
from doktok_contracts.schemas import EntityType

requires_gliner = pytest.mark.skipif(
    importlib.util.find_spec("gliner") is None,
    reason="gliner engine extra not installed (see providers/gliner, ADR-0023)",
)


@dataclass(frozen=True)
class Case:
    """One labeled fixture: a snippet and the job title it contains (None for negatives)."""

    text: str
    title: str | None = None
    note: str = ""


POSITIVES = [
    # German
    Case("Maria Weber ist Geschäftsführerin der Muster GmbH.", "Geschäftsführerin"),
    Case("Unser Steuerberater Herr Klein prüft die Unterlagen.", "Steuerberater"),
    Case("Die Rechtsanwältin Dr. Schmidt vertritt die Klägerin.", "Rechtsanwältin"),
    Case("Als Projektleiter verantwortet Herr Vogel den Umbau der Filiale.", "Projektleiter"),
    Case("Die Krankenschwester dokumentierte die Medikation um 8 Uhr.", "Krankenschwester"),
    Case("Der Bürgermeister eröffnete die Sitzung des Stadtrats.", "Bürgermeister"),
    # English
    Case("Jane Doe was promoted to Chief Financial Officer in March.", "Chief Financial Officer"),
    Case("A senior software engineer reviewed the pull request.", "software engineer"),
    Case("The data scientist trained a new forecasting model.", "data scientist"),
    Case("Our sales director signed the framework agreement.", "sales director"),
    Case("The nurse recorded the patient's temperature.", "nurse"),
    Case("He works as a plumber in Manchester.", "plumber"),
]

NEGATIVES = [
    Case("Stefan Vogel unterschrieb den Vertrag gestern.", note="person name only"),
    Case("Die Siemens AG lieferte die Turbine nach Hamburg.", note="organisation + place"),
    Case("Der Tisch steht im Büro neben dem Fenster.", note="German common nouns"),
    Case("The invoice total is 100 euros, due at the end of next month.", note="invoice prose"),
    Case("Berlin ist die Hauptstadt von Deutschland.", note="places only"),
    Case("Bitte überweisen Sie den Betrag bis Freitag auf das Konto.", note="payment request"),
    Case("The meeting was moved to the large conference room.", note="English common nouns"),
    Case("Das Auto wurde am Montag vor die Garage geliefert.", note="delivery prose"),
]

# Gates: precision is the contract (Phase 1 discipline); recall is reported with a conservative
# floor so a model/threshold regression fails loudly without flaking on open-class variance.
FP_RATE_CEILING = 0.0
RECALL_FLOOR = 0.75


@pytest.fixture(scope="module")
def extractor():  # type: ignore[no-untyped-def]  # gliner types unavailable without the extra
    from doktok_provider_gliner import GlinerEntityNerExtractor

    return GlinerEntityNerExtractor()


def _titles(extractor, text: str) -> set[str]:  # type: ignore[no-untyped-def]
    return {
        e.normalized_value.casefold()
        for e in extractor.extract(text)
        if e.entity_type is EntityType.JOB_TITLE
    }


def _matches(expected: str, found: set[str]) -> bool:
    """Tolerant hit: the expected title and an extracted value contain each other (either way),
    so 'senior software engineer' counts as finding 'software engineer'."""
    want = expected.casefold()
    return any(want in got or got in want for got in found)


@requires_gliner
def test_job_title_false_positive_rate(extractor) -> None:  # type: ignore[no-untyped-def]
    """Precision side: no negative fixture may yield ANY JOB_TITLE."""
    hits = [(case.note, _titles(extractor, case.text)) for case in NEGATIVES]
    false_positives = [(note, found) for note, found in hits if found]
    fp_rate = len(false_positives) / len(NEGATIVES)
    assert fp_rate <= FP_RATE_CEILING, (
        f"JOB_TITLE false-positive rate {fp_rate:.2f} exceeds {FP_RATE_CEILING:.2f}; "
        f"fired on: {false_positives}"
    )


@requires_gliner
def test_job_title_recall(extractor) -> None:  # type: ignore[no-untyped-def]
    """Recall side: report the false negatives; gate at a conservative floor."""
    misses: list[tuple[str, str, set[str]]] = []
    for case in POSITIVES:
        assert case.title is not None
        found = _titles(extractor, case.text)
        if not _matches(case.title, found):
            misses.append((case.title, case.text, found))
    recall = 1 - len(misses) / len(POSITIVES)
    assert recall >= RECALL_FLOOR, (
        f"JOB_TITLE recall {recall:.2f} below floor {RECALL_FLOOR:.2f}; "
        f"missed ({len(misses)}/{len(POSITIVES)}): {misses}"
    )
