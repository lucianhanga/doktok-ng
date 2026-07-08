"""ADDRESS / POSTAL_CODE extraction via libpostal (#518 Phase 1) - an ENGINE EXTRA.

libpostal is a heavy C library (+ ~2GB trained data), so - like the OCR and reranker runtimes -
its Python binding (``postal``) is deliberately NOT in the lockfile. It is installed on demand via
``make address-libpostal`` (which needs the C lib, e.g. ``brew install libpostal``). At runtime we
probe availability once; when missing, address/postal detection is skipped entirely with a single
log line and every other detector keeps working (graceful fallback, mirrors the GLiNER probe in
apps/worker/composition.py).

Precision contract:
- Only line windows that already look address-like (street token + a 4-5 digit postcode shape)
  are handed to libpostal at all.
- An ADDRESS is emitted only when libpostal parses out a road AND a house number AND a 4-5 digit
  postcode from the window - a lone street name or a lone number never qualifies.
- POSTAL_CODE is emitted ONLY as the postcode component of such a parsed address, NEVER as a bare
  5-digit regex match.
"""

from __future__ import annotations

import importlib.util
import logging
import re
from collections.abc import Iterable
from typing import Protocol

from doktok_contracts.media import ExtractedEntity
from doktok_contracts.schemas import EntityType

logger = logging.getLogger("doktok.entities")


class _ParseAddress(Protocol):
    def __call__(self, address: str) -> list[tuple[str, str]]: ...


# Tri-state cache: None = not probed yet; a callable = available; False-y sentinel via _unavailable.
_parse_address: _ParseAddress | None = None
_unavailable = False


def libpostal_available() -> bool:
    """True when the ``postal`` binding is importable (engine extra installed)."""
    return importlib.util.find_spec("postal") is not None


def _resolve_parser() -> _ParseAddress | None:
    """Lazy-import ``postal.parser`` once; on any failure disable address extraction for the
    process lifetime and log a single hint (the feature must keep running without it)."""
    global _parse_address, _unavailable
    if _parse_address is not None:
        return _parse_address
    if _unavailable:
        return None
    try:
        if not libpostal_available():
            raise ModuleNotFoundError("No module named 'postal'")
        from postal.parser import parse_address  # heavy C extension - import stays lazy

        _parse_address = parse_address
        return _parse_address
    except Exception as exc:  # noqa: BLE001 - a load failure must degrade, never crash
        _unavailable = True
        logger.info(
            "libpostal not available (%s); ADDRESS/POSTAL_CODE extraction disabled "
            "(install via `make address-libpostal`)",
            exc,
        )
        return None


# A window qualifies for parsing only if it carries BOTH signals: a street-ish token and a
# postcode-shaped digit group followed by a capitalized word (the city). Everything else never
# reaches libpostal, which keeps throughput high and precision strict.
_STREET_CUE = re.compile(
    r"(?:straße|strasse|str\.|weg|gasse|platz|allee|ring|damm|chaussee|ufer"
    r"|road|street|avenue|lane|boulevard|drive)\b",
    re.IGNORECASE,
)
_POSTCODE_CITY = re.compile(r"\b(\d{4,5})\s+[A-ZÄÖÜ][a-zäöüß]")
_POSTCODE_SHAPE = re.compile(r"\d{4,5}")
# Cap libpostal calls per document: address blocks live near the top/bottom of letters and
# invoices; a pathological document must not turn into thousands of C-library calls.
_MAX_PARSES = 50


def _windows(text: str) -> Iterable[tuple[str, int, int]]:
    """Candidate (snippet, start, end) windows: each non-empty line alone, and each pair of
    consecutive non-empty lines (letterhead addresses wrap street / postcode onto two lines)."""
    lines: list[tuple[str, int]] = []  # (stripped line, absolute start offset)
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped:
            lines.append((stripped, offset + line.index(stripped[0])))
        offset += len(line)
    for index, (line_text, start) in enumerate(lines):
        yield line_text, start, start + len(line_text)
        if index + 1 < len(lines):
            next_text, next_start = lines[index + 1]
            # Only pair ADJACENT lines (no blank line in between was kept by construction, so
            # guard on proximity: the next line must start within 2 chars of this line's end).
            if next_start - (start + len(line_text)) <= 2:
                yield f"{line_text}, {next_text}", start, next_start + len(next_text)


def _components(parsed: list[tuple[str, str]]) -> dict[str, str]:
    """First value per libpostal label ('road', 'house_number', 'postcode', 'city', ...)."""
    out: dict[str, str] = {}
    for value, label in parsed:
        out.setdefault(label, value)
    return out


def extract_addresses(text: str) -> list[ExtractedEntity]:
    """Validated ADDRESS + POSTAL_CODE entities, or [] when libpostal is not installed."""
    parse = _resolve_parser()
    if parse is None:
        return []
    found: list[ExtractedEntity] = []
    seen_spans: list[tuple[int, int]] = []
    parses = 0
    for snippet, start, end in _windows(text):
        if parses >= _MAX_PARSES:
            break
        if not (_STREET_CUE.search(snippet) and _POSTCODE_CITY.search(snippet)):
            continue
        if any(start < s_end and s_start < end for s_start, s_end in seen_spans):
            continue  # the 2-line window already covered this line
        parses += 1
        parts = _components(parse(snippet))
        road = parts.get("road")
        house_number = parts.get("house_number")
        postcode = parts.get("postcode")
        # The validator: road + house number + a postcode-shaped postcode, or it is not an
        # address. libpostal labels almost anything, so ALL three components are required.
        if not road or not house_number or not postcode:
            continue
        if _POSTCODE_SHAPE.fullmatch(postcode) is None:
            continue
        seen_spans.append((start, end))
        city = parts.get("city", "")
        normalized = f"{road} {house_number}, {postcode} {city}".strip().rstrip(",")
        found.append(
            ExtractedEntity(
                entity_text=snippet,
                entity_type=EntityType.ADDRESS,
                normalized_value=normalized,
                start_offset=start,
                end_offset=end,
            )
        )
        # POSTAL_CODE only ever ships as a component of a parsed address (never a bare match).
        postcode_at = snippet.find(postcode)
        if postcode_at >= 0:
            found.append(
                ExtractedEntity(
                    entity_text=postcode,
                    entity_type=EntityType.POSTAL_CODE,
                    normalized_value=postcode,
                    start_offset=start + postcode_at,
                    end_offset=start + postcode_at + len(postcode),
                )
            )
    return found
