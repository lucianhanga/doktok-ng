"""Configuration objects for KG/KAG enrichment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .schemas import RelationSchemaRule, normalize_label


@dataclass(slots=True)
class EntityLinkingConfig:
    """Controls canonicalization of entity mentions into graph nodes."""

    # label -> {canonical_name: [aliases]} or label -> [canonical_name, ...]
    gazetteers: Mapping[str, Mapping[str, list[str]] | list[str] | set[str] | tuple[str, ...]] = (
        field(default_factory=dict)
    )
    fuzzy_threshold: float = 0.92
    use_fuzzy: bool = True
    case_sensitive: bool = False
    prefer_gazetteer_id: bool = True
    id_prefix_by_label: Mapping[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RelationRefinementConfig:
    """Controls relation filtering, validation, and triple conversion."""

    default_relation_threshold: float = 0.50
    relation_thresholds: Mapping[str, float] = field(default_factory=dict)
    low_confidence_threshold: float = 0.72
    keep_low_confidence: bool = True
    schema_rules: list[RelationSchemaRule] = field(default_factory=list)
    deduplicate: bool = True
    prefer_longer_entities: bool = True
    require_distinct_entities: bool = True
    evidence_window_chars: int = 240
    normalize_predicates: bool = True

    def threshold_for(self, predicate: str) -> float:
        norm = normalize_label(predicate)
        for key, value in self.relation_thresholds.items():
            if normalize_label(key) == norm:
                return float(value)
        return float(self.default_relation_threshold)

    def schema_for(self, predicate: str) -> RelationSchemaRule | None:
        norm = normalize_label(predicate)
        for rule in self.schema_rules:
            if normalize_label(rule.predicate) == norm:
                return rule
        return None


@dataclass(slots=True)
class RelexModelConfig:
    """GLiNER-Relex model settings."""

    model_name: str = "knowledgator/gliner-relex-large-v1.0"
    entity_threshold: float = 0.30
    relation_threshold: float = 0.50
    flat_ner: bool = False
    device: str | None = None
    map_location: str | None = None
    load_kwargs: dict[str, Any] = field(default_factory=dict)
    inference_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuleRelationConfig:
    """Simple deterministic relation extraction rules.

    Rules are intentionally small and transparent. They are useful for high precision
    domain patterns such as "X acquired Y", "X is CEO of Y", "X is located in Y".
    """

    enabled: bool = True
    max_span_distance_chars: int = 200
    patterns: Mapping[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class KAGEnrichmentConfig:
    """Top-level configuration for the enrichment pipeline."""

    entity_labels: list[str] | dict[str, str] = field(default_factory=list)
    relation_labels: list[str] | dict[str, str] = field(default_factory=list)
    include_other_entity_type: bool = False
    entity_linking: EntityLinkingConfig = field(default_factory=EntityLinkingConfig)
    relation_refinement: RelationRefinementConfig = field(default_factory=RelationRefinementConfig)
    relex_model: RelexModelConfig = field(default_factory=RelexModelConfig)
    rule_relations: RuleRelationConfig = field(default_factory=RuleRelationConfig)
    # The LLM fallback is passed at call time. These flags control when it is used.
    enable_llm_fallback: bool = False
    fallback_for_low_confidence: bool = True
    fallback_when_no_relations: bool = False
    max_fallback_items: int = 20

    def effective_entity_labels(self) -> list[str] | dict[str, str]:
        labels = self.entity_labels
        if self.include_other_entity_type:
            if isinstance(labels, dict):
                labels = dict(labels)
                labels.setdefault("other", "Entity type inferred from specified relations")
            else:
                labels = list(labels)
                if "other" not in [str(x).lower() for x in labels]:
                    labels.append("other")
        return labels


__all__ = [
    "EntityLinkingConfig",
    "RelationRefinementConfig",
    "RelexModelConfig",
    "RuleRelationConfig",
    "KAGEnrichmentConfig",
]
