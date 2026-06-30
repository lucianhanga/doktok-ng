from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from .config import label_key
from .types import Entity


@dataclass(frozen=True)
class RegexRule:
    name: str
    pattern: re.Pattern[str]
    score: float = 0.99
    normalizer: Callable[[str], str] | None = None


EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.I)
URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>()]+", re.I)
PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,4}[\s.-]?\d{3,4}(?!\w)"
)
# Conservative but useful for most English/German business text.
DATE_RE = re.compile(
    r"\b(?:"
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
    r"|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{2,4}"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{2,4}"
    r")\b",
    re.I,
)
CURRENCY_RE = re.compile(
    r"(?<!\w)(?:[$€£¥]\s?\d{1,3}(?:[,.\s]\d{3})*(?:[.,]\d+)?\s?(?:[KMBTkmbt]|mn|bn|million|billion)?|\d+(?:[.,]\d+)?\s?(?:[KMBTkmbt]\s?)?(?:USD|EUR|GBP|CHF|JPY|CNY|CAD|AUD|SEK|NOK|DKK))(?!\w)",
    re.I,
)
# Generic IDs (order, invoice, ticket, SKU). Conservative, to avoid overmatching every word.
ID_RE = re.compile(r"\b(?:ID|Id|id|INV|PO|SO|SKU|REF|CASE|TICKET)[-:#\s]?[A-Z0-9][A-Z0-9_-]{3,}\b")


DEFAULT_REGEX_RULES: dict[str, RegexRule] = {
    "email": RegexRule("email", EMAIL_RE, 0.995, lambda value: value.lower()),
    "url": RegexRule("url", URL_RE, 0.990, lambda value: value.rstrip(".,;:)")),
    "phone": RegexRule("phone", PHONE_RE, 0.960, lambda value: re.sub(r"\s+", " ", value.strip())),
    "date": RegexRule("date", DATE_RE, 0.930, lambda value: re.sub(r"\s+", " ", value.strip())),
    "currency": RegexRule(
        "currency", CURRENCY_RE, 0.970, lambda value: re.sub(r"\s+", " ", value.strip())
    ),
    "id": RegexRule("id", ID_RE, 0.950, lambda value: value.strip()),
}


def requested_regex_labels(
    labels: Iterable[str], regex_label_map: Mapping[str, str]
) -> dict[str, str]:
    requested = {label_key(label): str(label) for label in labels}
    active: dict[str, str] = {}
    for rule_name, mapped_label in regex_label_map.items():
        mapped_key = label_key(mapped_label)
        rule_key = label_key(rule_name)
        if mapped_key in requested:
            active[rule_name] = requested[mapped_key]
        elif rule_key in requested:
            active[rule_name] = requested[rule_key]
    return active


def regex_entities(
    text: str, labels: Iterable[str], regex_label_map: Mapping[str, str]
) -> list[Entity]:
    active = requested_regex_labels(labels, regex_label_map)
    entities: list[Entity] = []
    for rule_name, output_label in active.items():
        rule = DEFAULT_REGEX_RULES.get(rule_name)
        if not rule:
            continue
        for match in rule.pattern.finditer(text):
            value = match.group(0)
            normalized = rule.normalizer(value) if rule.normalizer else value
            entities.append(
                Entity(
                    text=value,
                    label=output_label,
                    start=match.start(),
                    end=match.end(),
                    score=rule.score,
                    source=f"regex:{rule.name}",
                    normalized=normalized,
                )
            )
    return entities


def validates_against_regex(
    label: str, text: str, regex_label_map: Mapping[str, str]
) -> bool | None:
    """
    Return True/False when label is a regex-backed label, otherwise None.
    """
    lk = label_key(label)
    for rule_name, mapped_label in regex_label_map.items():
        if lk not in {label_key(rule_name), label_key(mapped_label)}:
            continue
        rule = DEFAULT_REGEX_RULES.get(rule_name)
        if not rule:
            return None
        return bool(rule.pattern.fullmatch(text.strip()))
    return None


def word_boundary_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    if re.search(r"\w", term[:1]) and re.search(r"\w", term[-1:]):
        return re.compile(rf"(?<!\w){escaped}(?!\w)", re.I)
    return re.compile(escaped, re.I)
