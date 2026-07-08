"""Validated structured-identifier detectors (#518 Phase 1): PHONE, IBAN, VAT_ID, TAX_NUMBER,
REGISTRATION_NUMBER.

Design principle: PRECISION over recall. A candidate becomes an entity only after a real validator
accepts it - never on a bare regex hit:

- PHONE: libphonenumber (``phonenumbers``) candidate matching + ``is_valid_number``, and
  additionally an international prefix or a nearby textual cue (Tel/Fax/...). FAX numbers are
  typed PHONE (kept simple on purpose; the cue that found them is not stored).
- IBAN: ISO 13616 - country code must be registered, length must match that country, and the
  ISO 7064 mod-97 checksum must be 1. A random digit string passes all three with ~1% probability
  at best; a corrupted IBAN fails the checksum.
- VAT_ID: per-country EU format. Where a check-digit algorithm is implemented (DE, AT) the
  checksum must pass; for other countries a VAT context cue (USt/VAT/UID/...) is REQUIRED nearby.
- TAX_NUMBER (German Steuernummer): the xx/xxx/xxxxx notation plus a REQUIRED "Steuernummer"/
  "St.-Nr." cue nearby - the digit pattern alone is far too generic to trust.
- REGISTRATION_NUMBER (Handelsregister & co.): HRB/HRA/GnR + number (the register token itself is
  the cue); the ambiguous VR/PR tokens additionally require a court/register keyword nearby.

Detectors run in priority order and later detectors skip spans already claimed (e.g. the digit run
inside an accepted IBAN can never double as a phone number).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import phonenumbers
from doktok_contracts.media import ExtractedEntity
from doktok_contracts.schemas import EntityType

# --------------------------------------------------------------------------------------------
# span bookkeeping: detectors run in priority order; a later detector never claims text that an
# earlier one already accepted (prevents e.g. IBAN digit runs re-surfacing as phone candidates).
# --------------------------------------------------------------------------------------------

_Span = tuple[int, int]


def _overlaps(span: _Span, taken: list[_Span]) -> bool:
    start, end = span
    return any(start < t_end and t_start < end for t_start, t_end in taken)


# --------------------------------------------------------------------------------------------
# IBAN (ISO 13616): registered country + exact registered length + mod-97 checksum == 1.
# --------------------------------------------------------------------------------------------

# Official IBAN registry lengths (country code -> total length of the compact IBAN).
_IBAN_LENGTHS: dict[str, int] = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22,
    "BH": 22, "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28, "CZ": 24, "DE": 22,
    "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24, "FI": 18, "FO": 18, "FR": 27,
    "GB": 22, "GE": 22, "GI": 23, "GL": 18, "GR": 27, "GT": 28, "HR": 21, "HU": 28,
    "IE": 22, "IL": 23, "IQ": 23, "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20,
    "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "LY": 25, "MC": 27,
    "MD": 24, "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15,
    "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "SA": 24,
    "SC": 31, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "ST": 25, "SV": 28, "TL": 23,
    "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}  # fmt: skip

# Candidate shape: CCkk then groups of 4 (the common print layout) or one compact run. Anything
# the mod-97/length/registry gate rejects is dropped, so this can stay permissive about spacing.
_IBAN_CANDIDATE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")


def iban_is_valid(compact: str) -> bool:
    """True iff ``compact`` (no spaces, uppercase) is a checksum-valid, registry-known IBAN."""
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", compact):
        return False
    expected = _IBAN_LENGTHS.get(compact[:2])
    if expected is None or len(compact) != expected:
        return False
    # ISO 7064 mod-97: move the first 4 chars to the end, map A..Z -> 10..35, must be == 1.
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def _find_ibans(text: str, taken: list[_Span]) -> Iterable[ExtractedEntity]:
    for match in _IBAN_CANDIDATE.finditer(text):
        compact = match.group(0).replace(" ", "")
        if not iban_is_valid(compact):
            continue
        span = (match.start(), match.end())
        if _overlaps(span, taken):
            continue
        taken.append(span)
        yield ExtractedEntity(
            entity_text=match.group(0),
            entity_type=EntityType.IBAN,
            normalized_value=compact,
            start_offset=span[0],
            end_offset=span[1],
        )


# --------------------------------------------------------------------------------------------
# VAT_ID (EU USt-IdNr): per-country format; checksum where implemented (DE, AT), otherwise a
# REQUIRED VAT cue nearby - a bare well-formed string is not enough.
# --------------------------------------------------------------------------------------------

# Country -> full regex for the part after the 2-letter country code (EU VAT number formats).
_VAT_FORMATS: dict[str, re.Pattern[str]] = {
    "AT": re.compile(r"U\d{8}"),
    "BE": re.compile(r"[01]\d{9}"),
    "BG": re.compile(r"\d{9,10}"),
    "CY": re.compile(r"\d{8}[A-Z]"),
    "CZ": re.compile(r"\d{8,10}"),
    "DE": re.compile(r"\d{9}"),
    "DK": re.compile(r"\d{8}"),
    "EE": re.compile(r"\d{9}"),
    "EL": re.compile(r"\d{9}"),
    "ES": re.compile(r"[A-Z0-9]\d{7}[A-Z0-9]"),
    "FI": re.compile(r"\d{8}"),
    "FR": re.compile(r"[A-HJ-NP-Z0-9]{2}\d{9}"),
    "HR": re.compile(r"\d{11}"),
    "HU": re.compile(r"\d{8}"),
    "IE": re.compile(r"\d{7}[A-W][A-I]?"),
    "IT": re.compile(r"\d{11}"),
    "LT": re.compile(r"\d{9}(?:\d{3})?"),
    "LU": re.compile(r"\d{8}"),
    "LV": re.compile(r"\d{11}"),
    "MT": re.compile(r"\d{8}"),
    "NL": re.compile(r"\d{9}B\d{2}"),
    "PL": re.compile(r"\d{10}"),
    "PT": re.compile(r"\d{9}"),
    "RO": re.compile(r"\d{2,10}"),
    "SE": re.compile(r"\d{12}"),
    "SI": re.compile(r"\d{8}"),
    "SK": re.compile(r"\d{10}"),
}

_VAT_CANDIDATE = re.compile(r"\b([A-Z]{2}) ?([0-9A-Z]{8,12})\b")
# Countries whose check digit we verify; for these the checksum replaces the cue requirement.
_VAT_CHECKSUM_COUNTRIES = frozenset({"DE", "AT"})
_VAT_CUE = re.compile(
    r"\b(?:USt[.\- ]?(?:Id(?:Nr)?)?|Umsatzsteuer|VAT|UID|MwSt|TVA|BTW|IVA)\b[-.:\s]*",
    re.IGNORECASE,
)


def _de_vat_checksum_ok(digits: str) -> bool:
    """German USt-IdNr check digit: ISO 7064 MOD 11,10 over the first 8 digits."""
    product = 10
    for ch in digits[:8]:
        total = (int(ch) + product) % 10
        if total == 0:
            total = 10
        product = (2 * total) % 11
    return (11 - product) % 10 == int(digits[8])


def _at_vat_checksum_ok(number: str) -> bool:
    """Austrian ATU check digit: doubled even positions with digit-sum, check = (96 - sum) % 10."""
    digits = number[1:]  # strip the leading 'U'
    total = 0
    for index, ch in enumerate(digits[:7]):
        value = int(ch)
        if index % 2 == 1:  # 2nd, 4th, 6th digit (1-based even positions) are doubled
            value *= 2
            value = value // 10 + value % 10
        total += value
    return (96 - total) % 10 == int(digits[7])


def _vat_checksum_ok(country: str, number: str) -> bool:
    if country == "DE":
        return _de_vat_checksum_ok(number)
    if country == "AT":
        return _at_vat_checksum_ok(number)
    return False


def _has_cue_before(text: str, start: int, cue: re.Pattern[str], window: int) -> bool:
    """True if ``cue`` occurs in the ``window`` characters before offset ``start``."""
    return cue.search(text[max(0, start - window) : start]) is not None


def _find_vat_ids(text: str, taken: list[_Span]) -> Iterable[ExtractedEntity]:
    for match in _VAT_CANDIDATE.finditer(text):
        country, number = match.group(1), match.group(2)
        fmt = _VAT_FORMATS.get(country)
        if fmt is None or fmt.fullmatch(number) is None:
            continue
        if country in _VAT_CHECKSUM_COUNTRIES:
            if not _vat_checksum_ok(country, number):
                continue  # well-formed but checksum-invalid -> reject, never guess
        elif not _has_cue_before(text, match.start(), _VAT_CUE, 24):
            continue  # no checksum implemented -> the VAT cue is REQUIRED
        span = (match.start(), match.end())
        if _overlaps(span, taken):
            continue
        taken.append(span)
        yield ExtractedEntity(
            entity_text=match.group(0),
            entity_type=EntityType.VAT_ID,
            normalized_value=f"{country}{number}",
            start_offset=span[0],
            end_offset=span[1],
        )


# --------------------------------------------------------------------------------------------
# TAX_NUMBER (German Steuernummer): xx(x)/xxx(x)/xxxx(x) state notation + REQUIRED cue nearby.
# The digit shape alone matches dates/file numbers, so no cue means no entity.
# --------------------------------------------------------------------------------------------

_TAX_CANDIDATE = re.compile(r"\b\d{2,3}\s?/\s?\d{3,4}\s?/\s?\d{4,5}\b")
_TAX_CUE = re.compile(r"\bSt(?:euer)?[.\- ]{0,2}Nr|\bSteuernummer\b", re.IGNORECASE)


def _find_tax_numbers(text: str, taken: list[_Span]) -> Iterable[ExtractedEntity]:
    for match in _TAX_CANDIDATE.finditer(text):
        if not _has_cue_before(text, match.start(), _TAX_CUE, 40):
            continue
        span = (match.start(), match.end())
        if _overlaps(span, taken):
            continue
        taken.append(span)
        yield ExtractedEntity(
            entity_text=match.group(0),
            entity_type=EntityType.TAX_NUMBER,
            normalized_value=re.sub(r"\s", "", match.group(0)),
            start_offset=span[0],
            end_offset=span[1],
        )


# --------------------------------------------------------------------------------------------
# REGISTRATION_NUMBER (Handelsregister & co.): the register token IS the required cue. HRB/HRA/
# GnR are distinctive enough alone; VR/PR are common abbreviations, so they additionally need a
# court/register keyword nearby (Amtsgericht/Handelsregister/...).
# --------------------------------------------------------------------------------------------

_REG_CANDIDATE = re.compile(r"\b(HRB|HRA|GnR|VR|PR)\s*[.:]?\s*(\d{1,6})(?: ?([A-Z]{1,3}))?\b")
_REG_AMBIGUOUS = frozenset({"VR", "PR"})
_REG_COURT_CUE = re.compile(
    r"\b(?:Amtsgericht|Registergericht|Handelsregister|Vereinsregister|Partnerschaftsregister)\b",
    re.IGNORECASE,
)


def _find_registration_numbers(text: str, taken: list[_Span]) -> Iterable[ExtractedEntity]:
    for match in _REG_CANDIDATE.finditer(text):
        register, number, suffix = match.group(1), match.group(2), match.group(3)
        if register in _REG_AMBIGUOUS and not _has_cue_before(
            text, match.start(), _REG_COURT_CUE, 60
        ):
            continue
        span = (match.start(), match.end())
        if _overlaps(span, taken):
            continue
        taken.append(span)
        normalized = f"{register} {number}" + (f" {suffix}" if suffix else "")
        yield ExtractedEntity(
            entity_text=match.group(0),
            entity_type=EntityType.REGISTRATION_NUMBER,
            normalized_value=normalized,
            start_offset=span[0],
            end_offset=span[1],
        )


# --------------------------------------------------------------------------------------------
# PHONE / FAX: libphonenumber finds + validates candidates. On top of validity we require either
# an international prefix (+49 / 0049) or a nearby cue (Tel/Fax/...), because a nationally-formed
# digit run (invoice totals, customer ids) can be a "valid" number by shape alone.
# --------------------------------------------------------------------------------------------

# Region national-format numbers are interpreted in. International (+..) numbers parse regardless.
_PHONE_DEFAULT_REGION = "DE"
_PHONE_CUE = re.compile(
    r"\b(?:tel(?:efon)?|fon|fax|telefax|phone|mobil(?:e)?|handy)\b", re.IGNORECASE
)


def _find_phones(text: str, taken: list[_Span]) -> Iterable[ExtractedEntity]:
    for match in phonenumbers.PhoneNumberMatcher(text, _PHONE_DEFAULT_REGION):
        if not phonenumbers.is_valid_number(match.number):  # matcher leniency already checks;
            continue  # kept explicit so the precision contract survives a leniency change
        raw = match.raw_string.strip()
        has_intl_prefix = raw.startswith("+") or raw.startswith("00")
        if not has_intl_prefix and not _has_cue_before(text, match.start, _PHONE_CUE, 24):
            continue  # a bare national digit run is too risky - reject (FAX cue counts as PHONE)
        span = (match.start, match.end)
        if _overlaps(span, taken):
            continue
        taken.append(span)
        yield ExtractedEntity(
            entity_text=match.raw_string,
            entity_type=EntityType.PHONE,
            normalized_value=phonenumbers.format_number(
                match.number, phonenumbers.PhoneNumberFormat.E164
            ),
            start_offset=span[0],
            end_offset=span[1],
        )


def extract_validated_entities(text: str) -> list[ExtractedEntity]:
    """All validated structured identifiers in ``text``, in detector priority order.

    IBAN runs first (longest, most specific), then VAT/registration/tax, then phone; each later
    detector skips spans an earlier one accepted, so one digit run yields at most one entity.
    """
    taken: list[_Span] = []
    found: list[ExtractedEntity] = []
    found.extend(_find_ibans(text, taken))
    found.extend(_find_vat_ids(text, taken))
    found.extend(_find_registration_numbers(text, taken))
    found.extend(_find_tax_numbers(text, taken))
    found.extend(_find_phones(text, taken))
    return found
