"""Runtime-pluggable fallback helpers.

No LLM provider is hard-coded here. Pass a callable at runtime, e.g. one that
calls OpenAI, Anthropic, Gemini, Ollama, vLLM, or your internal service.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .schemas import (
    EntityMention,
    KGTriple,
    LowConfidenceItem,
    RelationMention,
    TextChunk,
    ensure_text_chunk,
    normalize_label,
)
from .utils import evidence_window, find_entity_by_text


class FallbackAdapter:
    """Normalize arbitrary fallback outputs into RelationMention/KGTriple objects."""

    def __init__(self, source: str = "llm_fallback"):
        self.source = source

    def run(
        self,
        fallback_callable,
        *,
        text: str | TextChunk,
        entity_labels: Sequence[str] | dict[str, str],
        relation_labels: Sequence[str] | dict[str, str],
        entities: Sequence[EntityMention],
        low_confidence: Sequence[LowConfidenceItem],
        context: dict[str, Any] | None = None,
    ) -> tuple[list[RelationMention], list[KGTriple]]:
        chunk = ensure_text_chunk(text)
        raw = fallback_callable(
            text=chunk.text,
            entity_labels=entity_labels,
            relation_labels=relation_labels,
            entities=[e.to_dict() for e in entities],
            low_confidence=[i.to_dict() for i in low_confidence],
            context=context or {},
        )
        return self.normalize(raw, entities=entities, text=chunk.text)

    def normalize(
        self,
        raw: Any,
        *,
        entities: Sequence[EntityMention],
        text: str,
    ) -> tuple[list[RelationMention], list[KGTriple]]:
        relations: list[RelationMention] = []
        triples: list[KGTriple] = []
        if raw is None:
            return relations, triples
        if isinstance(raw, (RelationMention, KGTriple)):
            raw = [raw]
        if isinstance(raw, Mapping):
            if "relations" in raw or "triples" in raw:
                for item in raw.get("relations", []) or []:
                    rel = self._dict_to_relation(item, entities, text)
                    if rel:
                        relations.append(rel)
                for item in raw.get("triples", []) or []:
                    triple = self._dict_to_triple(item)
                    if triple:
                        triples.append(triple)
                return relations, triples
            raw = [raw]

        for item in raw:
            if isinstance(item, RelationMention):
                relations.append(item)
            elif isinstance(item, KGTriple):
                triples.append(item)
            elif isinstance(item, Mapping):
                rel = self._dict_to_relation(item, entities, text)
                if rel is not None:
                    relations.append(rel)
                else:
                    triple = self._dict_to_triple(item)
                    if triple:
                        triples.append(triple)
        return relations, triples

    def _dict_to_relation(
        self, item: Mapping[str, Any], entities: Sequence[EntityMention], text: str
    ) -> RelationMention | None:
        pred = item.get("predicate") or item.get("relation") or item.get("label")
        if not pred:
            return None
        subj_value = (
            item.get("subject") or item.get("head") or item.get("subj") or item.get("subject_name")
        )
        obj_value = (
            item.get("object") or item.get("tail") or item.get("obj") or item.get("object_name")
        )
        subj = self._coerce_entity(subj_value, entities)
        obj = self._coerce_entity(obj_value, entities)
        if subj is None or obj is None:
            return None
        return RelationMention(
            subject=subj,
            predicate=normalize_label(str(pred)),
            object=obj,
            score=float(item.get("score", item.get("confidence", 0.74))),
            evidence_text=item.get("evidence_text") or evidence_window(text, subj, obj),
            source=self.source,
            qualifiers=dict(item.get("qualifiers", {}) or {}),
            metadata={"raw": dict(item)},
        )

    def _dict_to_triple(self, item: Mapping[str, Any]) -> KGTriple | None:
        required = ["subject_id", "predicate", "object_id"]
        if not all(k in item for k in required):
            return None
        return KGTriple(
            subject_id=str(item["subject_id"]),
            subject_name=str(item.get("subject_name", item["subject_id"])),
            subject_label=normalize_label(str(item.get("subject_label", "entity"))),
            predicate=normalize_label(str(item["predicate"])),
            object_id=str(item["object_id"]),
            object_name=str(item.get("object_name", item["object_id"])),
            object_label=normalize_label(str(item.get("object_label", "entity"))),
            confidence=float(item.get("confidence", item.get("score", 0.74))),
            evidence_text=item.get("evidence_text"),
            source_doc_id=item.get("source_doc_id"),
            source_chunk_id=item.get("source_chunk_id"),
            qualifiers=dict(item.get("qualifiers", {}) or {}),
            provenance=dict(item.get("provenance", {}) or {"relation_source": self.source}),
            metadata=dict(item.get("metadata", {}) or {}),
        )

    @staticmethod
    def _coerce_entity(value: Any, entities: Sequence[EntityMention]) -> EntityMention | None:
        if isinstance(value, EntityMention):
            return value
        if isinstance(value, Mapping):
            text = str(value.get("text") or value.get("name") or value.get("canonical_name") or "")
            label = str(value.get("label") or value.get("type") or "")
            found = find_entity_by_text(entities, text, label or None)
            if found:
                return found
            return EntityMention(
                text=text,
                label=normalize_label(label or "entity"),
                score=float(value.get("score", value.get("confidence", 0.70))),
                source="llm_fallback",
                canonical_id=value.get("canonical_id") or value.get("entity_id"),
                canonical_name=value.get("canonical_name") or value.get("name") or text,
            )
        if isinstance(value, str):
            return find_entity_by_text(entities, value) or EntityMention(
                text=value, label="entity", score=0.70, source="llm_fallback"
            )
        return None


__all__ = ["FallbackAdapter"]
