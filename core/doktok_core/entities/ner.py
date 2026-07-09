"""LLM-assisted named-entity recognition support (M7.4).

PERSON / ORG / GPE / JOB_TITLE can't be found with regex (they need to recognise *names* and
open-class role phrases), so a model-backed ``EntityNerExtractor`` fills them. NER and the
rule-based ``EntitiesFeature`` write to the same ``document_entities`` table but own DISJOINT
entity-type sets, so each can re-run (backfill, retry, version bump) without clobbering the
other's rows.
"""

from __future__ import annotations

import re

from doktok_contracts.schemas import EntityType

# The entity types owned by the NER feature. Everything else belongs to the rule-based extractor.
# JOB_TITLE (#518 Phase 2) is model-based like PERSON/ORG/GPE (an open-class semantic type, not a
# validated pattern), so the NER feature owns it too and it populates on re-extraction of "ner".
NER_ENTITY_TYPES: tuple[EntityType, ...] = (
    EntityType.PERSON,
    EntityType.ORG,
    EntityType.GPE,
    EntityType.JOB_TITLE,
)

_WHITESPACE = re.compile(r"\s+")
# Same approach as enrichment.categories.normalize_category: everything that is not a word
# character or whitespace becomes a separator, so 'hanga,lucian' tokenizes like 'hanga lucian'.
_PUNCTUATION = re.compile(r"[^\w\s]")

# German-style address lines fuse postal code and city ("80287 München"), and NER models return
# that whole span as ONE place mention - so every distinct PLZ minted its own city node (#528).
# The split below peels the code off so all variants collapse into one city node.
#
# Precision guardrails (prefer under-splitting to wrong-splitting):
# - exactly 4-5 leading digits (the libpostal postcode shape from entities.address, DE/AT/CH),
#   anchored at the very start and delimited by whitespace - a 6+ digit run or mid-string
#   number never matches;
# - the remainder must start with a Unicode LETTER ([^\W\d_]) and have >= 2 characters, so a
#   bare digit run ("80287"), a digit-initial remainder ("80287 2nd") or an empty tail stay
#   untouched;
# - '.' does not cross newlines, so multi-line values never split.
_POSTAL_PLACE = re.compile(r"^(\d{4,5})\s+([^\W\d_].+)$")

# Metadata contract for the POSTAL_CODE rows the NER feature derives from that split. `source`
# scopes row OWNERSHIP: the rule-based EntitiesFeature owns every other POSTAL_CODE row
# (libpostal address components) and must not delete/duplicate these, and vice-versa.
POSTAL_SOURCE_KEY = "source"
POSTAL_SOURCE_NER = "ner"
POSTAL_PLACE_KEY = "place"  # normalized place name the code was split from
POSTAL_PLACE_TYPE_KEY = "place_type"  # the place mention's entity type (GPE/LOCATION)
POSTAL_EVIDENCE_KEY = "evidence"  # the original fused span, e.g. "80287 München"


def split_postal_place(value: str) -> tuple[str, str] | None:
    """``("80287", "München")`` when ``value`` is a PLZ-fused place name, else ``None``.

    Only a genuine leading postal-code shape splits (see ``_POSTAL_PLACE`` guardrails); when in
    doubt the mention is left unchanged.
    """
    match = _POSTAL_PLACE.match(value.strip())
    if match is None:
        return None
    return match.group(1), match.group(2).strip()


def normalize_ner_name(name: str) -> str:
    """A whitespace-collapsed, casefolded key for de-duplicating a name across its mentions."""
    return _WHITESPACE.sub(" ", name.strip()).casefold()


def normalize_entity_name(name: str) -> str:
    """The token-set sort key for entity resolution (#508): casefold, strip punctuation,
    collapse whitespace, then SORT and DEDUPE the tokens.

    This is deliberately stronger than ``normalize_ner_name`` (which stays the per-document
    display normalization): deriving the KG node key from this value collapses word-order and
    punctuation variants at write time - 'lucian hanga', 'hanga,lucian' and 'hanga lucian' all
    key to 'hanga lucian'. Single-token concatenations ('lucianhanga') and typos ('hanja lucian')
    stay distinct keys; those are the fuzzy tier's job (``knowledge_graph.entity_resolution``).

    Falls back to the casefolded, whitespace-collapsed input when punctuation-stripping leaves
    nothing (an all-punctuation value must not collapse into the empty key).
    """
    text = _PUNCTUATION.sub(" ", name).casefold().strip()
    tokens = sorted(set(text.split()))
    if not tokens:
        return normalize_ner_name(name)
    return " ".join(tokens)
