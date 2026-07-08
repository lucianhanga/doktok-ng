"""Precision gate for the validated structured-entity detectors (#518 Phase 1).

Labeled fixtures per entity type with BOTH positives and tricky negatives (checksum-corrupted
IBANs, invoice numbers that look like phones, bare 5-digit runs that are not postal codes, ...).
The acceptance criterion is a FALSE-POSITIVE RATE of zero per type over the negative fixtures:
every value a validator accepts must really be an instance of that type. Recall is asserted on
the positives so the detectors stay useful, but precision is the contract.

Address/postal fixtures need libpostal (an engine extra, `make address-libpostal`) and skip
cleanly when it is not installed - like the other engine-extra tests.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from doktok_contracts.schemas import EntityType
from doktok_core.entities.address import extract_addresses, libpostal_available
from doktok_core.entities.extractor import RegexEntityExtractor
from doktok_core.entities.validated import extract_validated_entities, iban_is_valid


@dataclass(frozen=True)
class Case:
    """One labeled fixture: a text snippet and the values the detector must (not) find."""

    text: str
    expected: set[str]  # normalized values that MUST be found (positives)
    forbidden_note: str = ""  # for negatives: why accepting anything here would be a FP


def _values(text: str, entity_type: EntityType) -> set[str]:
    return {
        e.normalized_value for e in extract_validated_entities(text) if e.entity_type is entity_type
    }


def _false_positive_rate(negatives: list[Case], entity_type: EntityType) -> float:
    """Share of negative fixtures on which the detector emits ANY value of the given type."""
    if not negatives:
        return 0.0
    hits = sum(1 for case in negatives if _values(case.text, entity_type))
    return hits / len(negatives)


# ---------------------------------------------------------------------------------------------
# IBAN: mod-97 + registry length. Negatives include checksum-corrupted and wrong-length strings.
# ---------------------------------------------------------------------------------------------

IBAN_POSITIVES = [
    Case("Bitte überweisen Sie auf IBAN DE89 3704 0044 0532 0130 00.", {"DE89370400440532013000"}),
    Case("Konto: DE89370400440532013000 (Commerzbank)", {"DE89370400440532013000"}),
    Case("IBAN: GB29 NWBK 6016 1331 9268 19", {"GB29NWBK60161331926819"}),
    Case("Account AT61 1904 3002 3457 3201 for transfers", {"AT611904300234573201"}),
    Case("FR14 2004 1010 0505 0001 3M02 606 est notre IBAN", {"FR1420041010050500013M02606"}),
]

IBAN_NEGATIVES = [
    Case("IBAN DE89 3704 0044 0532 0130 01 bitte prüfen", set(), "checksum-corrupted last digit"),
    Case("DE89370400440532013001", set(), "compact but checksum-invalid"),
    Case("DE12 3456 7890 1234 56", set(), "wrong length for DE (18 != 22)"),
    Case("XX89 3704 0044 0532 0130 00", set(), "unregistered country code"),
    Case("Order ID AB12CDEF3456789012345678", set(), "IBAN-shaped product code, invalid checksum"),
    Case("Referenz: DE00 0000 0000 0000 0000 00", set(), "all zeros fails mod-97"),
]


def test_iban_recall() -> None:
    for case in IBAN_POSITIVES:
        assert _values(case.text, EntityType.IBAN) == case.expected, case.text


def test_iban_false_positive_rate_is_zero() -> None:
    assert _false_positive_rate(IBAN_NEGATIVES, EntityType.IBAN) == 0.0


def test_iban_validator_unit() -> None:
    assert iban_is_valid("DE89370400440532013000")
    assert not iban_is_valid("DE89370400440532013001")  # bad check digits
    assert not iban_is_valid("DE8937040044053201300")  # bad length
    assert not iban_is_valid("ZZ89370400440532013000")  # unknown country


# ---------------------------------------------------------------------------------------------
# PHONE: libphonenumber validity + (international prefix OR cue). Negatives are digit runs that
# must never be typed as phone numbers: invoice ids, IBAN fragments, order numbers, dates.
# ---------------------------------------------------------------------------------------------

PHONE_POSITIVES = [
    Case("Tel.: 089 1234567", {"+49891234567"}),
    Case("Rufen Sie uns an: +49 30 901820", {"+4930901820"}),
    Case("Telefon 030/901820, Fax 030/901821", {"+4930901820", "+4930901821"}),
    Case("Fax: +49 (0)89 12 34 56 7", {"+49891234567"}),
    Case("Support hotline 0049 89 1234567 (Mo-Fr)", {"+49891234567"}),
]

PHONE_NEGATIVES = [
    Case("Rechnungsnummer 2026-4711-089123", set(), "invoice number is not a phone"),
    Case("Bestellnummer: 089 1234567", set(), "phone-shaped order number without phone cue"),
    Case("Artikelnummer 4711-0815, Menge 100", set(), "article number"),
    Case("Kundennummer 1234567890", set(), "bare national digit run without cue"),
    Case("Seite 3 von 12, Beleg 20260610", set(), "date-like digit run"),
    Case("Das Hotel 089 liegt zentral", set(), "'Hotel' must not fire the 'tel' cue"),
]


def test_phone_recall() -> None:
    for case in PHONE_POSITIVES:
        assert _values(case.text, EntityType.PHONE) == case.expected, case.text


def test_phone_false_positive_rate_is_zero() -> None:
    assert _false_positive_rate(PHONE_NEGATIVES, EntityType.PHONE) == 0.0


# ---------------------------------------------------------------------------------------------
# VAT_ID: format + checksum (DE/AT) or format + required cue (other countries).
# ---------------------------------------------------------------------------------------------

VAT_POSITIVES = [
    Case("USt-IdNr.: DE136695976", {"DE136695976"}),  # checksum-valid German VAT id
    Case("Unsere Umsatzsteuer-ID lautet DE 136695976.", {"DE136695976"}),
    Case("UID: ATU13585627", {"ATU13585627"}),  # checksum-valid Austrian VAT id
    Case("VAT number: NL123456782B12", {"NL123456782B12"}),  # cue-gated country
]

VAT_NEGATIVES = [
    Case("USt-IdNr.: DE123456789", set(), "well-formed but checksum-invalid DE VAT"),
    Case("DE136695977 steht auf dem Etikett", set(), "checksum-invalid, one digit off"),
    Case("UID: ATU12345678", set(), "checksum-invalid AT VAT"),
    Case("Produktcode NL123456782B12 im Katalog", set(), "cue-gated country without VAT cue"),
    Case("Container IT12345678901 verschifft", set(), "IT-format digits without VAT cue"),
    Case("Angebot DE20260610A gültig bis Juni", set(), "DE-prefixed reference, wrong format"),
]


def test_vat_recall() -> None:
    for case in VAT_POSITIVES:
        assert _values(case.text, EntityType.VAT_ID) == case.expected, case.text


def test_vat_false_positive_rate_is_zero() -> None:
    assert _false_positive_rate(VAT_NEGATIVES, EntityType.VAT_ID) == 0.0


# ---------------------------------------------------------------------------------------------
# TAX_NUMBER: Steuernummer notation + REQUIRED cue. The bare digit shape is a date/file number.
# ---------------------------------------------------------------------------------------------

TAX_POSITIVES = [
    Case("Steuernummer: 151/815/08156", {"151/815/08156"}),
    Case("St.-Nr. 93/815/08152 beim Finanzamt München", {"93/815/08152"}),
    Case("Steuer-Nr: 181/815/08155", {"181/815/08155"}),
]

TAX_NEGATIVES = [
    Case("Aktenzeichen 12/345/6789 der Behörde", set(), "file number without tax cue"),
    Case("Am 12/06/2026 geliefert", set(), "date in slash notation"),
    Case("Los 151/815/08156 wurde versteigert", set(), "tax-shaped number without cue"),
    Case(
        "Steuernummer folgt separat per Post. Ihre Bestellung 12/345/67890 wurde bestätigt.",
        set(),
        "cue too far away from the digits",
    ),
]


def test_tax_number_recall() -> None:
    for case in TAX_POSITIVES:
        assert _values(case.text, EntityType.TAX_NUMBER) == case.expected, case.text


def test_tax_number_false_positive_rate_is_zero() -> None:
    assert _false_positive_rate(TAX_NEGATIVES, EntityType.TAX_NUMBER) == 0.0


# ---------------------------------------------------------------------------------------------
# REGISTRATION_NUMBER: HRB/HRA/GnR fire on the register token itself; VR/PR need a court cue.
# ---------------------------------------------------------------------------------------------

REG_POSITIVES = [
    Case("Amtsgericht München, HRB 86891", {"HRB 86891"}),
    Case("eingetragen unter HRA 12345 beim Registergericht", {"HRA 12345"}),
    Case("Handelsregister: HRB123456 B", {"HRB 123456 B"}),
    Case("Vereinsregister Amtsgericht Köln VR 4711", {"VR 4711"}),
]

REG_NEGATIVES = [
    Case(
        "Unsere PR 2026 Kampagne startet im Juni", set(), "PR (public relations) is not a register"
    ),
    Case("Die VR 360 Brille kostet 299 Euro", set(), "VR (virtual reality) product"),
    Case("HRB ohne Nummer erwähnt", set(), "register token without a number"),
]


def test_registration_number_recall() -> None:
    for case in REG_POSITIVES:
        assert _values(case.text, EntityType.REGISTRATION_NUMBER) == case.expected, case.text


def test_registration_number_false_positive_rate_is_zero() -> None:
    assert _false_positive_rate(REG_NEGATIVES, EntityType.REGISTRATION_NUMBER) == 0.0


# ---------------------------------------------------------------------------------------------
# ADDRESS / POSTAL_CODE (libpostal engine extra): a postal code is emitted ONLY as part of a
# parsed address. Skips cleanly when libpostal is not installed.
# ---------------------------------------------------------------------------------------------

requires_libpostal = pytest.mark.skipif(
    not libpostal_available(), reason="libpostal not installed (make address-libpostal)"
)

ADDRESS_POSITIVES = [
    Case("Musterstraße 12, 80331 München", {"80331"}),
    Case("Firma GmbH\nHauptstraße 5\n10115 Berlin", {"10115"}),
]

ADDRESS_NEGATIVES = [
    Case("Die Bestellnummer 80331 wurde storniert.", set(), "bare 5-digit run is not a PLZ"),
    Case("Artikel 10115 ist ausverkauft, 80331 auf Lager.", set(), "digit runs without an address"),
    Case("Im Jahr 80331 v. Chr. gab es keine Straßen.", set(), "number in prose"),
]


@requires_libpostal
def test_address_recall() -> None:
    for case in ADDRESS_POSITIVES:
        postal_codes = {
            e.normalized_value
            for e in extract_addresses(case.text)
            if e.entity_type is EntityType.POSTAL_CODE
        }
        addresses = [e for e in extract_addresses(case.text) if e.entity_type is EntityType.ADDRESS]
        assert postal_codes == case.expected, case.text
        assert addresses, case.text


@requires_libpostal
def test_postal_code_false_positive_rate_is_zero() -> None:
    hits = 0
    for case in ADDRESS_NEGATIVES:
        found = {
            e.normalized_value
            for e in extract_addresses(case.text)
            if e.entity_type in (EntityType.POSTAL_CODE, EntityType.ADDRESS)
        }
        if found:
            hits += 1
    assert hits / len(ADDRESS_NEGATIVES) == 0.0


def test_postal_code_never_bare_without_libpostal_or_address() -> None:
    """A bare 5-digit run must never surface as POSTAL_CODE regardless of libpostal presence."""
    found = RegexEntityExtractor().extract("Die Bestellnummer 80331 wurde storniert.")
    assert all(e.entity_type is not EntityType.POSTAL_CODE for e in found)


def test_address_emission_with_stub_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    """The libpostal wiring itself (component gate + POSTAL_CODE-only-with-address), exercised
    with a stub parser so CI covers it without the engine extra installed."""
    from doktok_core.entities import address as address_module

    def stub_parse(text: str) -> list[tuple[str, str]]:
        if "hauptstraße" in text.lower():
            return [
                ("hauptstraße", "road"),
                ("5", "house_number"),
                ("10115", "postcode"),
                ("berlin", "city"),
            ]
        # Address-shaped but incomplete: libpostal labels something, the gate must reject it.
        return [("80331", "postcode")]

    monkeypatch.setattr(address_module, "_parse_address", stub_parse)
    monkeypatch.setattr(address_module, "_unavailable", False)

    found = address_module.extract_addresses("Firma GmbH\nHauptstraße 5\n10115 Berlin")
    types = {e.entity_type for e in found}
    assert types == {EntityType.ADDRESS, EntityType.POSTAL_CODE}
    postal = next(e for e in found if e.entity_type is EntityType.POSTAL_CODE)
    assert postal.normalized_value == "10115"

    # A window that parses to postcode-only (no road/house number) must emit NOTHING.
    incomplete = address_module.extract_addresses("Lagerstraße unbekannt\n80331 Irgendwo")
    assert incomplete == []


# ---------------------------------------------------------------------------------------------
# Cross-detector precision: one digit run must yield at most one typed entity, and the mixed
# document fixture (the realistic acceptance case) must type every identifier correctly.
# ---------------------------------------------------------------------------------------------


def test_iban_digits_never_double_as_phone_or_vat() -> None:
    text = "Tel: IBAN DE89 3704 0044 0532 0130 00"  # adversarial: phone cue right before an IBAN
    entities = extract_validated_entities(text)
    types = {e.entity_type for e in entities}
    assert types == {EntityType.IBAN}


def test_mixed_german_invoice_footer() -> None:
    footer = (
        "Muster GmbH - Amtsgericht München HRB 86891\n"
        "USt-IdNr.: DE136695976 - Steuernummer: 143/815/08152\n"
        "Tel.: +49 89 1234567 - Fax: 089 7654321\n"
        "IBAN: DE89 3704 0044 0532 0130 00 - BIC: COBADEFFXXX\n"
        "Rechnungsnummer 2026-08154711, Kundennummer 987654321\n"
    )
    by_type: dict[EntityType, set[str]] = {}
    for entity in extract_validated_entities(footer):
        by_type.setdefault(entity.entity_type, set()).add(entity.normalized_value)
    assert by_type[EntityType.IBAN] == {"DE89370400440532013000"}
    assert by_type[EntityType.VAT_ID] == {"DE136695976"}
    assert by_type[EntityType.TAX_NUMBER] == {"143/815/08152"}
    assert by_type[EntityType.REGISTRATION_NUMBER] == {"HRB 86891"}
    assert by_type[EntityType.PHONE] == {"+49891234567", "+49897654321"}
    # Precision: the invoice/customer numbers must NOT be typed as anything.
    all_values = {v for values in by_type.values() for v in values}
    assert "2026-08154711" not in all_values
    assert "987654321" not in all_values
