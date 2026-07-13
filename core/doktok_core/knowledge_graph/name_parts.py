"""Parse a PERSON entity's surface name into structured parts (given / middle / family).

Best-practice ruling (#531, data agent): a person is ONE node; name parts are ATTRIBUTES stored on
the node's ``metadata``, never token nodes (token nodes destroy identity and create surname hub
nodes that poison merge-adjudication neighbourhoods). These parts feed only the shared-surname
family-discovery hint (#532); they NEVER enter the entity-MERGE cascade.

Library choice - ``nameparser`` (HumanName), not probablepeople: probablepeople's person/corporation
classifier mislabels the non-US (German / Romanian) names this corpus is full of - it tags
"Lucian Cosmin Hanga" as a *Corporation* (dropping the surname entirely) and raises outright on
"Johann Wolfgang von Goethe". HumanName assumes the input is a person (correct - NER already typed
it PERSON), keeps particles (von / van der / de la) attached to the surname, and handles titles,
hyphenated names, and comma-inverted ("Cotirlea, Viviana") forms.
"""

from __future__ import annotations

import re
from typing import Any

from nameparser import HumanName

_HAS_DIGIT = re.compile(r"\d")

# Below this we treat the parse as too weak to assert a surname; store nothing rather than a guess.
_MIN_CONFIDENCE = 0.5


def parse_person_name(surface: str) -> dict[str, Any] | None:
    """Structured name parts for a PERSON surface form, or ``None`` when no family name is safe.

    Returns only the keys we are confident about:
    ``{given_name?, middle_names?[list], family_name, name_parse_confidence}``. Returns ``None``
    (the caller stores nothing) when the surface cannot yield a trustworthy surname - a single
    token ("Angela", "München"), any digits (not a real personal name), an empty parse, or a
    low-confidence split. We never fabricate a wrong surname.
    """
    text = (surface or "").strip()
    if not text or _HAS_DIGIT.search(text):
        return None
    # A family name needs at least two tokens; a lone token is a given name or a place, not one.
    if len(text.split()) < 2:
        return None

    name = HumanName(text)
    family = name.last.strip()
    if not family:
        return None

    confidence = _confidence(name, text)
    if confidence < _MIN_CONFIDENCE:
        return None

    parts: dict[str, Any] = {"family_name": family, "name_parse_confidence": confidence}
    given = name.first.strip()
    if given:
        parts["given_name"] = given
    middles = name.middle.split()
    if middles:
        parts["middle_names"] = middles
    return parts


def _confidence(name: HumanName, surface: str) -> float:
    """A coarse structural-plausibility score in [0, 1]. HumanName is deterministic and gives no
    score of its own, so this reflects how clean the split looks, not a model probability."""
    score = 1.0
    if not name.first.strip():
        score -= 0.3  # a surname with no given name is a weaker "this is a full person name" signal
    if len(surface.split()) > 4:
        score -= 0.2  # long strings are more often titles/orgs misread as people
    return round(max(score, 0.0), 2)
