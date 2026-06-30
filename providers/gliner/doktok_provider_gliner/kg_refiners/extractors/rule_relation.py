"""High-precision rule-based relation extraction.

This module is intentionally simple. Add domain-specific patterns before relying
on a model fallback: many KG relations are templatic and can be captured with
transparent rules.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..config import RuleRelationConfig
from ..schemas import EntityMention, RelationMention, TextChunk, ensure_text_chunk, normalize_label
from ..utils import evidence_window, normalize_text, sentence_bounds
from .base import BaseKGExtractor

DEFAULT_PATTERNS: dict[str, list[str]] = {
    "acquired": [r"\bacquir(?:ed|es|ing|e)\b", r"\bbought\b", r"\bpurchased\b"],
    "founded by": [r"\bfounded by\b", r"\bco-founded by\b"],
    "works for": [r"\bworks? (?:for|at)\b", r"\bemployed by\b", r"\bjoined\b"],
    "located in": [r"\blocated in\b", r"\bbased in\b", r"\bheadquartered in\b"],
    "part of": [r"\bpart of\b", r"\bsubsidiary of\b", r"\bowned by\b"],
    "partnered with": [r"\bpartner(?:ed|s)? with\b", r"\bcollaborat(?:ed|es|ing) with\b"],
}


class RuleRelationExtractor(BaseKGExtractor):
    """Extract relations from existing entities with regex triggers."""

    def __init__(self, config: RuleRelationConfig | None = None):
        self.config = config or RuleRelationConfig()

    def extract(
        self,
        text: str | TextChunk,
        entity_labels: Sequence[str] | dict[str, str],
        relation_labels: Sequence[str] | dict[str, str],
        *,
        entities: Sequence[EntityMention] | None = None,
        **kwargs,
    ) -> tuple[list[EntityMention], list[RelationMention]]:
        chunk = ensure_text_chunk(text)
        if not self.config.enabled or not entities:
            return list(entities or []), []

        relation_names = (
            list(relation_labels.keys())
            if isinstance(relation_labels, dict)
            else list(relation_labels)
        )
        pattern_map = {**DEFAULT_PATTERNS, **dict(self.config.patterns)}
        allowed_patterns = {
            normalize_label(name): pattern_map.get(normalize_label(name), [])
            for name in relation_names
        }
        relations: list[RelationMention] = []
        sorted_entities = sorted(
            entities, key=lambda e: (e.start if e.start is not None else 10**12, e.end or 10**12)
        )

        for i, subj in enumerate(sorted_entities):
            if subj.start is None or subj.end is None:
                continue
            for obj in sorted_entities[i + 1 :]:
                if obj.start is None or obj.end is None:
                    continue
                if abs((obj.start or 0) - (subj.end or 0)) > self.config.max_span_distance_chars:
                    continue
                left = min(subj.start, obj.start)
                right = max(subj.end or subj.start, obj.end or obj.start)
                s0, s1 = sentence_bounds(chunk.text, left, right)
                sentence = chunk.text[s0:s1]
                between_start = min(subj.end, obj.end)
                between_end = max(subj.start, obj.start)
                between = chunk.text[between_start:between_end]

                for predicate, patterns in allowed_patterns.items():
                    if not patterns:
                        continue
                    if any(
                        re.search(pat, sentence, flags=re.IGNORECASE) for pat in patterns
                    ) or any(re.search(pat, between, flags=re.IGNORECASE) for pat in patterns):
                        relations.append(
                            RelationMention(
                                subject=subj,
                                predicate=predicate,
                                object=obj,
                                score=0.86,
                                evidence_text=normalize_text(sentence)
                                or evidence_window(chunk.text, subj, obj),
                                source="rule_relation",
                                metadata={"rule": predicate},
                            )
                        )
        return list(entities), relations


__all__ = ["RuleRelationExtractor"]
