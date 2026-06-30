from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass

from .config import GazetteerValue, label_key
from .rules import word_boundary_pattern
from .types import Entity

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - optional dependency
    fuzz = None


@dataclass(frozen=True)
class GazetteerAlias:
    label: str
    canonical: str
    alias: str


def iter_aliases(gazetteers: Mapping[str, GazetteerValue]) -> Iterator[GazetteerAlias]:
    for label, values in gazetteers.items():
        if isinstance(values, Mapping):
            for canonical, aliases in values.items():
                yield GazetteerAlias(str(label), str(canonical), str(canonical))
                for alias in aliases:
                    yield GazetteerAlias(str(label), str(canonical), str(alias))
        else:
            for value in values:
                yield GazetteerAlias(str(label), str(value), str(value))


def exact_gazetteer_entities(
    text: str,
    labels: Iterable[str],
    gazetteers: Mapping[str, GazetteerValue],
) -> list[Entity]:
    requested = {label_key(label): str(label) for label in labels}
    entities: list[Entity] = []
    seen = set()
    for item in iter_aliases(gazetteers):
        if label_key(item.label) not in requested or not item.alias.strip():
            continue
        output_label = requested[label_key(item.label)]
        for match in word_boundary_pattern(item.alias).finditer(text):
            key = (match.start(), match.end(), label_key(output_label), item.canonical)
            if key in seen:
                continue
            seen.add(key)
            entities.append(
                Entity(
                    text=text[match.start() : match.end()],
                    label=output_label,
                    start=match.start(),
                    end=match.end(),
                    score=0.985,
                    source="gazetteer:exact",
                    normalized=item.canonical,
                )
            )
    return entities


def _token_spans(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[\w][\w&.'/-]*", text)]


def fuzzy_gazetteer_entities(
    text: str,
    labels: Iterable[str],
    gazetteers: Mapping[str, GazetteerValue],
    fuzzy_threshold: int = 92,
    max_aliases: int = 2_000,
    max_text_chars: int = 20_000,
) -> list[Entity]:
    """
    Conservative fuzzy matching over token windows.

    This is useful for aliases/misspellings but intentionally capped so it does not
    become an accidental O(n*m) bottleneck on large documents or huge dictionaries.
    For very large gazetteers, use a dedicated search index/trie instead.
    """
    if fuzz is None or len(text) > max_text_chars:
        return []

    requested = {label_key(label): str(label) for label in labels}
    aliases = [
        item
        for item in iter_aliases(gazetteers)
        if label_key(item.label) in requested and item.alias.strip()
    ]
    if not aliases or len(aliases) > max_aliases:
        return []

    tokens = _token_spans(text)
    if not tokens:
        return []

    entities: list[Entity] = []
    seen = set()
    for item in aliases:
        alias_tokens = _token_spans(item.alias)
        if not alias_tokens:
            continue
        output_label = requested[label_key(item.label)]
        target_len = len(alias_tokens)
        window_sizes = sorted({max(1, target_len - 1), target_len, target_len + 1})
        alias_l = item.alias.lower()
        for size in window_sizes:
            if size > len(tokens):
                continue
            for i in range(0, len(tokens) - size + 1):
                start = tokens[i][1]
                end = tokens[i + size - 1][2]
                candidate = text[start:end]
                if candidate.lower() == alias_l:
                    continue  # exact matcher handles this with higher precision
                score = fuzz.WRatio(candidate.lower(), alias_l)
                if score < fuzzy_threshold:
                    continue
                key = (start, end, label_key(output_label), item.canonical)
                if key in seen:
                    continue
                seen.add(key)
                entities.append(
                    Entity(
                        text=candidate,
                        label=output_label,
                        start=start,
                        end=end,
                        score=score / 100.0,
                        source="gazetteer:fuzzy",
                        normalized=item.canonical,
                        metadata={"matched_alias": item.alias, "fuzzy_score": score},
                    )
                )
    return entities
