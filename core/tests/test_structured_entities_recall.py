"""Recall / false-negative report for the validated structured-entity detectors (#518 Phase 1).

The precision gate (``test_structured_entities_precision.py``) proves the detectors emit no false
positives. This is the complement: given entities that SHOULD be found, how many are MISSED.

Because the detectors are precision-first (they reject anything that does not validate), realistic
but imperfect input - OCR noise, a missing cue word, an odd layout - is deliberately MISSED rather
than wrongly grabbed. This test makes those false negatives visible per type:

  * CLEAN cases  - well-formed input; MUST be caught (a regression gate).
  * HARD cases   - imperfect input; a miss is a *documented* false negative (the precision cost),
                   reported but not required.

The printed report shows, per type, recall = found / expected and the specific misses.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from doktok_contracts.schemas import EntityType
from doktok_core.entities.validated import extract_validated_entities


@dataclass(frozen=True)
class Want:
    text: str
    value: str  # the value that SHOULD be extracted
    hard: bool = False  # True => imperfect input; a miss here is a documented false negative
    note: str = ""


def _found(text: str, entity_type: EntityType, value: str) -> bool:
    return value in {
        e.normalized_value for e in extract_validated_entities(text) if e.entity_type is entity_type
    }


# Reuse KNOWN-VALID synthetic values from the precision fixtures; HARD variants add OCR/format noise
# (each HARD miss is a documented false negative - the price of precision-first validation).
CASES: dict[EntityType, list[Want]] = {
    EntityType.IBAN: [
        Want("IBAN DE89 3704 0044 0532 0130 00 zur Zahlung", "DE89370400440532013000"),
        Want("Konto DE89370400440532013000", "DE89370400440532013000"),
        Want("iban de89 3704 0044 0532 0130 00", "DE89370400440532013000", True, "lowercase"),
        Want(
            "IBAN DE89 37O4 0044 0532 O130 00",
            "DE89370400440532013000",
            True,
            "OCR O->0 breaks mod-97",
        ),
    ],
    EntityType.PHONE: [
        Want("Tel.: 089 1234567", "+49891234567"),
        Want("Anruf unter +49 30 901820", "+4930901820"),
        Want(
            "089 1234567 erreichbar Mo-Fr",
            "+49891234567",
            True,
            "valid number, no cue + no intl prefix",
        ),
        Want("Tel: 0800-BLUMEN", "+498002586636", True, "vanity number with letters"),
    ],
    EntityType.VAT_ID: [
        Want("USt-IdNr.: DE136695976", "DE136695976"),
        Want("VAT number NL123456782B12", "NL123456782B12"),
        Want(
            "Code NL123456782B12 gelistet",
            "NL123456782B12",
            True,
            "cue-gated country without a VAT cue",
        ),
    ],
    EntityType.TAX_NUMBER: [
        Want("Steuernummer: 151/815/08156", "151/815/08156"),
        Want(
            "Ref. 151/815/08156 notiert",
            "151/815/08156",
            True,
            "tax-shaped number without a tax cue",
        ),
    ],
    EntityType.REGISTRATION_NUMBER: [
        Want("Amtsgericht München, HRB 86891", "HRB 86891"),
        Want("Vereinsregister Amtsgericht Köln VR 4711", "VR 4711"),
        Want("VR 4711", "VR 4711", True, "ambiguous VR without a court cue"),
    ],
}


def test_ner_recall_report(capsys: pytest.CaptureFixture[str]) -> None:
    lines = ["", "NER recall / false-negative report (#518 Phase 1):"]
    for etype, wants in CASES.items():
        clean = [w for w in wants if not w.hard]
        clean_hits = [w for w in clean if _found(w.text, etype, w.value)]
        all_hits = [w for w in wants if _found(w.text, etype, w.value)]
        misses = [w for w in wants if not _found(w.text, etype, w.value)]
        lines.append(
            f"  {etype.value:20} recall {len(all_hits)}/{len(wants)}"
            f" (clean {len(clean_hits)}/{len(clean)})"
        )
        for w in misses:
            tag = "false-negative (by design)" if w.hard else "REGRESSION"
            lines.append(f"      MISS [{tag}] {w.note or w.value!r}")
    # Regression gate: every well-formed (CLEAN) case must still be extracted.
    for etype, wants in CASES.items():
        clean = [w for w in wants if not w.hard]
        missed_clean = [w.text for w in clean if not _found(w.text, etype, w.value)]
        assert not missed_clean, f"{etype.value}: clean recall dropped for {missed_clean}"
    with capsys.disabled():
        print("\n".join(lines))
