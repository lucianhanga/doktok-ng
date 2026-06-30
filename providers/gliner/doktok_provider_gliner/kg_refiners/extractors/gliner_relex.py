"""GLiNER-Relex wrapper.

This wrapper is deliberately defensive around output shapes. Current model-card
usage for knowledgator/gliner-relex-large-v1.0 is:

    entities, relations = model.inference(
        texts=[text], labels=entity_labels, relations=relation_labels,
        threshold=0.3, relation_threshold=0.5, return_relations=True,
        flat_ner=False
    )

The library normalizes the returned dictionaries into dataclasses.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..config import RelexModelConfig
from ..schemas import EntityMention, RelationMention, TextChunk, ensure_text_chunk, normalize_label
from ..utils import evidence_window, find_entity_by_text, repair_entity
from .base import BaseKGExtractor


class GLiNERRelexExtractor(BaseKGExtractor):
    """Zero-shot joint entity and relation extractor using GLiNER-Relex."""

    def __init__(self, config: RelexModelConfig | None = None, model: Any | None = None):
        self.config = config or RelexModelConfig()
        self.model = model

    def load(self) -> Any:
        if self.model is not None:
            return self.model
        try:
            from gliner import GLiNER  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "GLiNER-Relex requires the optional GLiNER dependency. "
                "Install with: pip install kg-refiners[gliner] or pip install gliner -U"
            ) from exc

        kwargs = dict(self.config.load_kwargs)
        if self.config.map_location is not None:
            kwargs.setdefault("map_location", self.config.map_location)
        self.model = GLiNER.from_pretrained(self.config.model_name, **kwargs)
        return self.model

    def extract(
        self,
        text: str | TextChunk,
        entity_labels: Sequence[str] | dict[str, str],
        relation_labels: Sequence[str] | dict[str, str],
        *,
        entities: Sequence[EntityMention] | None = None,
        **kwargs: Any,
    ) -> tuple[list[EntityMention], list[RelationMention]]:
        chunk = ensure_text_chunk(text)
        model = self.load()
        threshold = kwargs.pop("threshold", self.config.entity_threshold)
        relation_threshold = kwargs.pop("relation_threshold", self.config.relation_threshold)
        flat_ner = kwargs.pop("flat_ner", self.config.flat_ner)

        inference_kwargs = dict(self.config.inference_kwargs)
        inference_kwargs.update(kwargs)
        raw = model.inference(
            texts=[chunk.text],
            labels=entity_labels,
            relations=relation_labels,
            threshold=threshold,
            relation_threshold=relation_threshold,
            return_relations=True,
            flat_ner=flat_ner,
            **inference_kwargs,
        )
        raw_entities, raw_relations = self._split_output(raw)
        parsed_entities = [
            repair_entity(self._parse_entity(e), chunk.text) for e in self._first(raw_entities)
        ]

        # If user supplied refined NER entities, retain them too. Relation heads/tails are
        # matched against the combined set.
        if entities:
            parsed_entities = [*entities, *parsed_entities]

        parsed_relations = [
            self._parse_relation(r, parsed_entities, chunk.text) for r in self._first(raw_relations)
        ]
        parsed_relations = [r for r in parsed_relations if r is not None]
        return parsed_entities, parsed_relations  # type: ignore[return-value]

    @staticmethod
    def _split_output(raw: Any) -> tuple[Any, Any]:
        if isinstance(raw, tuple) and len(raw) == 2:
            return raw[0], raw[1]
        if isinstance(raw, Mapping):
            return raw.get("entities", []), raw.get("relations", [])
        # Some wrappers may return [ {entities, relations} ]
        if isinstance(raw, list) and raw and isinstance(raw[0], Mapping):
            return [raw[0].get("entities", [])], [raw[0].get("relations", [])]
        return [], []

    @staticmethod
    def _first(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            if value and isinstance(value[0], list):
                return value[0]
            return value
        return list(value) if not isinstance(value, (str, bytes)) else []

    @staticmethod
    def _parse_entity(raw: Mapping[str, Any]) -> EntityMention:
        text = str(raw.get("text", raw.get("span", raw.get("entity", ""))))
        start = raw.get("start", raw.get("start_pos", raw.get("start_idx")))
        end = raw.get("end", raw.get("end_pos", raw.get("end_idx")))
        return EntityMention(
            text=text,
            label=normalize_label(str(raw.get("label", raw.get("type", "entity")))),
            start=int(start) if start is not None else None,
            end=int(end) if end is not None else None,
            score=float(raw.get("score", raw.get("confidence", 1.0))),
            source="gliner_relex",
            metadata={"raw": dict(raw)},
        )

    def _parse_relation(
        self,
        raw: Mapping[str, Any],
        entities: Sequence[EntityMention],
        text: str,
    ) -> RelationMention | None:
        head_raw = raw.get("head") or raw.get("subject") or raw.get("subj")
        tail_raw = raw.get("tail") or raw.get("object") or raw.get("obj")
        predicate = str(raw.get("relation", raw.get("predicate", raw.get("label", "related to"))))
        score = float(raw.get("score", raw.get("confidence", 1.0)))

        subject = self._coerce_entity(head_raw, entities, text)
        obj = self._coerce_entity(tail_raw, entities, text)
        if subject is None or obj is None:
            return None
        return RelationMention(
            subject=subject,
            predicate=normalize_label(predicate),
            object=obj,
            score=score,
            evidence_text=evidence_window(text, subject, obj),
            source="gliner_relex",
            metadata={"raw": dict(raw)},
        )

    @staticmethod
    def _coerce_entity(
        raw: Any, entities: Sequence[EntityMention], text: str
    ) -> EntityMention | None:
        if raw is None:
            return None
        if isinstance(raw, EntityMention):
            return raw
        if isinstance(raw, Mapping):
            parsed = GLiNERRelexExtractor._parse_entity(raw)
            match = find_entity_by_text(entities, parsed.text, parsed.label)
            return match or repair_entity(parsed, text)
        if isinstance(raw, str):
            return find_entity_by_text(entities, raw) or EntityMention(
                text=raw, label="entity", source="gliner_relex"
            )
        return None


__all__ = ["GLiNERRelexExtractor"]
