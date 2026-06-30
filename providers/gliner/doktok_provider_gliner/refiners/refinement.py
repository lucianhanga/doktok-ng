from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .config import RefinementConfig, canonical_label, label_key
from .gazetteer import exact_gazetteer_entities, fuzzy_gazetteer_entities
from .rules import regex_entities, validates_against_regex
from .types import Entity, ExtractionResult

RawEntity = Mapping[str, Any]
FallbackCallable = Callable[[str, Sequence[str], Sequence[Entity]], Sequence[RawEntity | Entity]]


class RefinementPipeline:
    """Post-process candidate entities from GLiNER, NuNER, or any compatible model."""

    def __init__(self, config: RefinementConfig | None = None):
        self.config = config or RefinementConfig()

    def refine(
        self,
        text: str,
        labels: Sequence[str],
        raw_entities: Sequence[RawEntity | Entity],
        *,
        frontier_fallback: FallbackCallable | None = None,
    ) -> ExtractionResult:
        requested_labels = [str(label) for label in labels]
        requested_keys = {label_key(label) for label in requested_labels}
        raw_model_entities = [self._raw_to_dict(entity) for entity in raw_entities]

        candidates: list[Entity] = []
        low_confidence: list[Entity] = []

        for raw in raw_entities:
            entity = self._coerce_entity(raw, text, requested_labels)
            if entity is None:
                continue
            entity = self._repair_span(entity, text)
            if entity is None:
                continue
            if label_key(entity.label) not in requested_keys:
                continue
            if not self._passes_validator(entity):
                continue

            threshold = self.config.threshold_for(entity.label)
            if entity.score >= threshold:
                candidates.append(entity)
            elif self.config.collect_low_confidence and entity.score >= max(
                0.01, threshold - self.config.low_confidence_margin
            ):
                low_confidence.append(entity)

        if self.config.enable_regex_rules:
            candidates.extend(regex_entities(text, requested_labels, self.config.regex_label_map))

        if self.config.enable_gazetteers and self.config.gazetteers:
            candidates.extend(
                exact_gazetteer_entities(text, requested_labels, self.config.gazetteers)
            )
            if self.config.enable_fuzzy_gazetteers:
                candidates.extend(
                    fuzzy_gazetteer_entities(
                        text,
                        requested_labels,
                        self.config.gazetteers,
                        fuzzy_threshold=self.config.fuzzy_threshold,
                        max_aliases=self.config.max_fuzzy_aliases,
                        max_text_chars=self.config.max_fuzzy_text_chars,
                    )
                )

        if frontier_fallback and low_confidence:
            fallback_raw = frontier_fallback(text, requested_labels, low_confidence)
            for raw in fallback_raw:
                entity = self._coerce_entity(raw, text, requested_labels)
                if entity is None:
                    continue
                entity = self._repair_span(entity, text)
                if entity is not None and self._passes_validator(entity):
                    candidates.append(
                        Entity(
                            text=entity.text,
                            label=entity.label,
                            start=entity.start,
                            end=entity.end,
                            score=entity.score,
                            source=entity.source
                            if entity.source != "model"
                            else "frontier_fallback",
                            normalized=entity.normalized,
                            metadata=entity.metadata,
                        )
                    )

        final_entities = self._deduplicate(candidates)
        final_entities = self._resolve_overlaps(final_entities)
        final_entities = sorted(final_entities, key=lambda item: (item.start, item.end, item.label))

        return ExtractionResult(
            entities=final_entities,
            low_confidence=sorted(
                low_confidence, key=lambda item: (item.start, item.end, item.label)
            ),
            raw_model_entities=raw_model_entities,
        )

    def _raw_to_dict(self, entity: RawEntity | Entity) -> dict[str, Any]:
        if isinstance(entity, Entity):
            return entity.to_dict()
        return dict(entity)

    def _coerce_entity(
        self,
        raw: RawEntity | Entity,
        text: str,
        labels: Sequence[str],
    ) -> Entity | None:
        if isinstance(raw, Entity):
            return raw

        label = raw.get("label") or raw.get("entity") or raw.get("type")
        if not label:
            return None
        label = canonical_label(str(label), labels)

        score = float(raw.get("score", raw.get("confidence", 1.0)))
        source = str(raw.get("source", "model"))
        normalized = raw.get("normalized") or raw.get("canonical")
        metadata = dict(raw.get("metadata", {}))

        if "start" in raw and "end" in raw:
            start = int(raw["start"])
            end = int(raw["end"])
            if start < 0 or end > len(text) or start >= end:
                return None
            ent_text = text[start:end]
        else:
            ent_text = str(raw.get("text") or raw.get("span") or "").strip()
            if not ent_text:
                return None
            pos = text.find(ent_text)
            if pos < 0:
                pos = text.lower().find(ent_text.lower())
            if pos < 0:
                return None
            start, end = pos, pos + len(ent_text)
            ent_text = text[start:end]

        return Entity(
            text=ent_text,
            label=label,
            start=start,
            end=end,
            score=score,
            source=source,
            normalized=str(normalized) if normalized is not None else None,
            metadata=metadata,
        )

    def _repair_span(self, entity: Entity, text: str) -> Entity | None:
        start, end = entity.start, entity.end
        trim = self.config.trim_chars
        while start < end and text[start] in trim:
            start += 1
        while end > start and text[end - 1] in trim:
            end -= 1
        if start >= end:
            return None
        repaired_text = text[start:end]
        normalized = (
            entity.normalized
            if entity.normalized is not None
            else self._normalize_value(entity.label, repaired_text)
        )
        return Entity(
            text=repaired_text,
            label=entity.label,
            start=start,
            end=end,
            score=entity.score,
            source=entity.source,
            normalized=normalized,
            metadata=entity.metadata,
        )

    def _normalize_value(self, label: str, value: str) -> str:
        lk = label_key(label)
        clean = " ".join(value.split())
        if lk in {"email", "url"}:
            return clean.lower().rstrip(".,;:)")
        if lk in {"organization", "company", "person", "location"}:
            return clean
        return clean

    def _passes_validator(self, entity: Entity) -> bool:
        if not self.config.validate_regex_labels:
            return True
        verdict = validates_against_regex(entity.label, entity.text, self.config.regex_label_map)
        if verdict is None:
            return True
        # Be strict for model predictions, but rule-created entities are already from regex.
        if entity.source.startswith("regex:"):
            return True
        return verdict

    def _deduplicate(self, entities: Sequence[Entity]) -> list[Entity]:
        grouped: dict[tuple[int, int, str, str], Entity] = {}
        for entity in entities:
            key = (
                entity.start,
                entity.end,
                label_key(entity.label),
                (entity.normalized or entity.text).casefold(),
            )
            previous = grouped.get(key)
            if previous is None or self._better(entity, previous) is entity:
                grouped[key] = entity
        return list(grouped.values())

    def _resolve_overlaps(self, entities: Sequence[Entity]) -> list[Entity]:
        if not entities:
            return []
        ordered = sorted(
            entities,
            key=lambda e: (
                -self._label_priority_score(e.label),
                -e.score,
                -(e.end - e.start),
                e.start,
            ),
        )
        selected: list[Entity] = []
        for entity in ordered:
            overlap_index = next(
                (i for i, chosen in enumerate(selected) if self._overlaps(entity, chosen)), None
            )
            if overlap_index is None:
                selected.append(entity)
                continue
            chosen = selected[overlap_index]
            better = self._better(entity, chosen)
            if better is entity:
                selected[overlap_index] = entity
        return selected

    def _better(self, a: Entity, b: Entity) -> Entity:
        # Prefer high-trust deterministic sources over fuzzy/model when score gap is small.
        if abs(a.score - b.score) <= self.config.score_close_delta:
            sa = self._source_priority_score(a.source)
            sb = self._source_priority_score(b.source)
            if sa != sb:
                return a if sa > sb else b

        # Prefer specific labels when score gap is small.
        pa = self._label_priority_score(a.label)
        pb = self._label_priority_score(b.label)
        if abs(a.score - b.score) <= self.config.score_close_delta and pa != pb:
            return a if pa > pb else b

        if (
            self.config.prefer_longer_spans
            and abs(a.score - b.score) <= self.config.score_close_delta
        ):
            la = a.end - a.start
            lb = b.end - b.start
            if la != lb:
                return a if la > lb else b

        if a.score != b.score:
            return a if a.score > b.score else b
        return a if (a.end - a.start) >= (b.end - b.start) else b

    @staticmethod
    def _source_priority_score(source: str) -> int:
        if source.startswith("regex:"):
            return 5
        if source == "gazetteer:exact":
            return 4
        if source == "frontier_fallback":
            return 3
        if source == "model":
            return 2
        if source == "gazetteer:fuzzy":
            return 1
        return 0

    def _label_priority_score(self, label: str) -> int:
        lk = label_key(label)
        priority = [label_key(item) for item in self.config.label_priority]
        try:
            return len(priority) - priority.index(lk)
        except ValueError:
            return 0

    @staticmethod
    def _overlaps(a: Entity, b: Entity) -> bool:
        return max(a.start, b.start) < min(a.end, b.end)
