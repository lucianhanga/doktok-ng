"""Utility functions used by the enrichment pipeline."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .schemas import EntityMention, RelationMention, normalize_label

_WHITESPACE = re.compile(r"\s+")
_BOUNDARY_PUNCT = " \t\n\r\f\v.,;:!?()[]{}<>\"'`“”‘’"


def normalize_text(value: str) -> str:
    return _WHITESPACE.sub(" ", str(value)).strip()


def repair_span(
    text: str, start: int | None, end: int | None
) -> tuple[str, int | None, int | None]:
    """Trim whitespace and edge punctuation while preserving offsets."""
    if start is None or end is None:
        return normalize_text(text), start, end
    start = max(0, int(start))
    end = min(len(text), int(end))
    while start < end and text[start] in _BOUNDARY_PUNCT:
        start += 1
    while end > start and text[end - 1] in _BOUNDARY_PUNCT:
        end -= 1
    return normalize_text(text[start:end]), start, end


def repair_entity(entity: EntityMention, full_text: str | None = None) -> EntityMention:
    if full_text is not None and entity.start is not None and entity.end is not None:
        fixed_text, start, end = repair_span(full_text, entity.start, entity.end)
    else:
        fixed_text = normalize_text(entity.text).strip(_BOUNDARY_PUNCT)
        start, end = entity.start, entity.end
    entity.text = fixed_text
    entity.start = start
    entity.end = end
    entity.label = normalize_label(entity.label)
    return entity


def entity_length(entity: EntityMention) -> int:
    if entity.start is not None and entity.end is not None:
        return max(0, entity.end - entity.start)
    return len(entity.text)


def overlap(a: EntityMention, b: EntityMention) -> bool:
    if a.start is None or a.end is None or b.start is None or b.end is None:
        return normalize_text(a.text).lower() == normalize_text(b.text).lower()
    return max(a.start, b.start) < min(a.end, b.end)


def same_entity(a: EntityMention, b: EntityMention) -> bool:
    if a.resolved_id() == b.resolved_id():
        return True
    if a.start is not None and b.start is not None:
        return (
            a.start == b.start
            and a.end == b.end
            and normalize_label(a.label) == normalize_label(b.label)
        )
    return normalize_text(a.text).lower() == normalize_text(b.text).lower() and normalize_label(
        a.label
    ) == normalize_label(b.label)


def dedupe_entities(
    entities: Sequence[EntityMention], *, prefer_longer: bool = True
) -> list[EntityMention]:
    """Remove duplicate or overlapping entity mentions."""
    ordered = sorted(
        entities,
        key=lambda e: (
            e.start if e.start is not None else 10**12,
            -(entity_length(e) if prefer_longer else 0),
            -float(e.score),
        ),
    )
    kept: list[EntityMention] = []
    for ent in ordered:
        if not ent.text:
            continue
        conflict_idx: int | None = None
        for i, prev in enumerate(kept):
            if normalize_label(ent.label) == normalize_label(prev.label) and overlap(ent, prev):
                conflict_idx = i
                break
            if ent.resolved_id() == prev.resolved_id() and overlap(ent, prev):
                conflict_idx = i
                break
        if conflict_idx is None:
            kept.append(ent)
            continue
        prev = kept[conflict_idx]
        choose_ent = False
        if (
            prefer_longer
            and entity_length(ent) > entity_length(prev)
            or entity_length(ent) == entity_length(prev)
            and ent.score > prev.score
        ):
            choose_ent = True
        if choose_ent:
            kept[conflict_idx] = ent
    return sorted(kept, key=lambda e: (e.start if e.start is not None else 10**12, e.end or 10**12))


def relation_key(rel: RelationMention) -> tuple[str, str, str]:
    return (rel.subject.resolved_id(), normalize_label(rel.predicate), rel.object.resolved_id())


def dedupe_relations(relations: Sequence[RelationMention]) -> list[RelationMention]:
    best: dict[tuple[str, str, str], RelationMention] = {}
    for rel in relations:
        key = relation_key(rel)
        cur = best.get(key)
        if (
            cur is None
            or rel.score > cur.score
            or len(rel.evidence_text or "") > len(cur.evidence_text or "")
        ):
            best[key] = rel
    return sorted(
        best.values(), key=lambda r: (-r.score, r.predicate, r.subject.text, r.object.text)
    )


def evidence_window(
    text: str, subject: EntityMention, obj: EntityMention, window_chars: int = 240
) -> str:
    starts = [x for x in [subject.start, obj.start] if x is not None]
    ends = [x for x in [subject.end, obj.end] if x is not None]
    if not starts or not ends:
        return normalize_text(text[:window_chars])
    start = max(0, min(starts) - window_chars // 2)
    end = min(len(text), max(ends) + window_chars // 2)
    return normalize_text(text[start:end])


def find_entity_by_text(
    entities: Sequence[EntityMention], value: str, label: str | None = None
) -> EntityMention | None:
    value_norm = normalize_text(value).lower()
    label_norm = normalize_label(label) if label else None
    best = None
    for ent in entities:
        if label_norm and normalize_label(ent.label) != label_norm:
            continue
        if (
            normalize_text(ent.text).lower() == value_norm
            or normalize_text(ent.resolved_name()).lower() == value_norm
        ) and (best is None or ent.score > best.score):
            best = ent
    return best


def sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    left = max(
        text.rfind(".", 0, start),
        text.rfind("!", 0, start),
        text.rfind("?", 0, start),
        text.rfind("\n", 0, start),
    )
    right_candidates = [
        idx
        for idx in [
            text.find(".", end),
            text.find("!", end),
            text.find("?", end),
            text.find("\n", end),
        ]
        if idx != -1
    ]
    right = min(right_candidates) if right_candidates else len(text)
    return left + 1, right + 1


def as_list_labels(labels: list[str] | dict[str, str]) -> list[str]:
    if isinstance(labels, dict):
        return list(labels.keys())
    return list(labels)


__all__ = [
    "normalize_text",
    "repair_span",
    "repair_entity",
    "dedupe_entities",
    "dedupe_relations",
    "evidence_window",
    "find_entity_by_text",
    "sentence_bounds",
    "as_list_labels",
]
