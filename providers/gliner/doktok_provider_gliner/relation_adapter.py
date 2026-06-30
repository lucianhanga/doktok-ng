"""Adapt the vendored GLiNER-Relex KAG pipeline to doktokNG's ``RelationExtractor`` port.

GLiNER-Relex (``knowledgator/gliner-relex-large-v1.0``) does joint entity + relation extraction in
one pass over open-vocabulary labels. doktokNG's relation port is closed-vocabulary and grounded:
``extract(text, entity_list)`` must return only triples whose subject/object are document entities
and whose predicate is one of ``PREDICATE_TYPE_PAIRS`` with matching subject/object types.

This adapter bridges the two: it asks GLiNER-Relex for natural-language phrasings of the closed
predicates, then maps each returned relation back to the canonical predicate, grounds both endpoints
to the supplied ``entity_list`` (by normalized name), assigns the doktok entity types, and validates
(and direction-corrects) against ``PREDICATE_TYPE_PAIRS`` - the single source of truth.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from doktok_contracts.media import ExtractedRelation
from doktok_core.entities.ner import normalize_ner_name
from doktok_core.knowledge_graph.predicates import PREDICATE_TYPE_PAIRS

from .kg_refiners import KAGEnricher, KAGEnrichmentConfig, RelexModelConfig
from .kg_refiners.extractors import BaseKGExtractor
from .kg_refiners.schemas import (
    EntityMention,
    RelationMention,
    TextChunk,
    ensure_text_chunk,
    normalize_label,
)
from .kg_refiners.utils import evidence_window, find_entity_by_text


class _GlinerRelexPipeline:
    """GLiNER-Relex one-pass entity+relation extraction via the relex model's native ``inference``.

    ``knowledgator/gliner-relex-large-v1.0`` loads as a custom ``UniEncoderSpanRelexGLiNER`` whose
    ``inference(texts, labels, relations, return_relations=True, ...)`` returns ``(entities,
    relations)`` in one pass; each relation is ``{head:{text,..}, tail:{..}, relation, score}``.
    This normalizes those to ``{source, relation, target, score}`` dicts for the adapter. (Uses the
    model's own method - not ``gliner.multitask``, whose package import pulls datasets/sklearn.)
    """

    def __init__(self, model: Any) -> None:
        self._model = model

    def __call__(
        self,
        texts: list[str],
        relations: Sequence[str],
        entities: Sequence[str],
        ner_threshold: float = 0.30,
        rel_threshold: float = 0.50,
    ) -> list[list[dict[str, Any]]]:
        _entities, rel_batch = self._model.inference(
            list(texts),
            labels=list(entities),
            relations=list(relations),
            threshold=ner_threshold,
            relation_threshold=rel_threshold,
            return_relations=True,
            flat_ner=True,
        )
        out: list[list[dict[str, Any]]] = []
        for text_rels in rel_batch:
            items: list[dict[str, Any]] = []
            for r in text_rels:
                head = r.get("head") or {}
                tail = r.get("tail") or {}
                source = str(head.get("text", "")).strip()
                target = str(tail.get("text", "")).strip()
                relation = str(r.get("relation", "")).strip()
                if not source or not target or not relation:
                    continue
                items.append(
                    {
                        "source": source,
                        "relation": relation,
                        "target": target,
                        "score": float(r.get("score", 1.0)),
                    }
                )
            out.append(items)
        return out


class _GlinerRelexExtractor(BaseKGExtractor):
    """A ``BaseKGExtractor`` running GLiNER-Relex native inference, feeding the kg_refiners flow.

    The pipeline returns, per text, ``{source, relation, target, score}`` dicts; they become
    ``RelationMention``s grounded to the caller-supplied ``entities`` by matching surface form.
    """

    def __init__(
        self,
        model_name: str,
        *,
        pipeline: Any = None,
        device: str | None = None,
        ner_threshold: float = 0.30,
        rel_threshold: float = 0.50,
    ) -> None:
        self._model_name = model_name
        self._pipeline = pipeline
        self._device = device
        self._ner_threshold = ner_threshold
        self._rel_threshold = rel_threshold

    def _get_pipeline(self) -> Any:
        if self._pipeline is None:
            from gliner import GLiNER

            model = GLiNER.from_pretrained(self._model_name)
            if self._device and hasattr(model, "to"):
                model.to(self._device)
            self._pipeline = _GlinerRelexPipeline(model)
        return self._pipeline

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
        labels = list(entity_labels) if entity_labels else ["named entity"]
        rels = list(relation_labels)
        batch = self._get_pipeline()(
            [chunk.text],
            relations=rels,
            entities=labels,
            ner_threshold=self._ner_threshold,
            rel_threshold=self._rel_threshold,
        )
        raw = batch[0] if batch else []
        seed = list(entities or [])
        relations: list[RelationMention] = []
        for item in raw:
            source = str(item.get("source", "")).strip()
            target = str(item.get("target", "")).strip()
            predicate = str(item.get("relation", "")).strip()
            if not source or not target or not predicate:
                continue
            subj = find_entity_by_text(seed, source) or EntityMention(
                text=source, label="entity", source="gliner_relex"
            )
            obj = find_entity_by_text(seed, target) or EntityMention(
                text=target, label="entity", source="gliner_relex"
            )
            relations.append(
                RelationMention(
                    subject=subj,
                    predicate=normalize_label(predicate),
                    object=obj,
                    score=float(item.get("score", 1.0)),
                    evidence_text=evidence_window(chunk.text, subj, obj),
                    source="gliner_relex",
                )
            )
        return seed, relations


# Natural-language phrasings GLiNER-Relex is asked to find, mapped to doktok's closed predicates.
# Multiple surfaces per predicate widen recall; the first listed is the canonical phrasing.
_PREDICATE_SURFACES: dict[str, list[str]] = {
    "EMPLOYED_BY": ["employed by", "works for"],
    "BANKS_WITH": ["banks with", "customer of bank"],
    "INSURED_BY": ["insured by", "policyholder of"],
    "CUSTOMER_OF": ["customer of"],
    "CONTRACTS_WITH": ["contracts with", "contracted with"],
    "REPRESENTED_BY": ["represented by"],
    "MEMBER_OF": ["member of"],
    "RESIDES_IN": ["resides in", "lives in"],
    "LOCATED_IN": ["located in", "based in"],
    "RELATED_TO": ["related to"],
}

# doktok entity types -> GLiNER-Relex coarse entity labels (joint NER half of the model).
_TYPE_TO_LABEL: dict[str, str] = {
    "PERSON": "person",
    "ORG": "organization",
    "GPE": "location",
    "LOCATION": "location",
}
_ENTITY_LABELS: dict[str, str] = {
    "person": "Human individual",
    "organization": "Company, institution, agency, or other organized group",
    "location": "Place, city, country, address, or region",
}

_MAX_CHARS = 12_000  # parity with the LLM relation extractor's input budget


class GlinerRelexRelationExtractor:
    """``RelationExtractor`` backed by GLiNER-Relex joint entity+relation extraction."""

    def __init__(
        self,
        model_name: str = "knowledgator/gliner-relex-large-v1.0",
        *,
        pipeline: Any = None,
        device: str | None = None,
        entity_threshold: float = 0.30,
        relation_threshold: float = 0.50,
        max_chars: int = _MAX_CHARS,
    ) -> None:
        self._surface_to_pred = {
            normalize_label(surface): predicate
            for predicate, surfaces in _PREDICATE_SURFACES.items()
            for surface in surfaces
        }
        relation_labels = [s for surfaces in _PREDICATE_SURFACES.values() for s in surfaces]
        relex_cfg = RelexModelConfig(
            model_name=model_name,
            device=device,
            map_location=device,
            entity_threshold=entity_threshold,
            relation_threshold=relation_threshold,
        )
        config = KAGEnrichmentConfig(
            entity_labels=dict(_ENTITY_LABELS),
            relation_labels=relation_labels,
            relex_model=relex_cfg,
        )
        extractor = _GlinerRelexExtractor(
            model_name,
            pipeline=pipeline,
            device=device,
            ner_threshold=entity_threshold,
            rel_threshold=relation_threshold,
        )
        self._enricher = KAGEnricher(config, extractor=extractor)
        self._max_chars = max_chars

    def extract(self, text: str, entity_list: list[tuple[str, str]]) -> list[ExtractedRelation]:
        if not entity_list:
            return []
        name2type: dict[str, str] = {}
        norm2name: dict[str, str] = {}
        for name, etype in entity_list:
            norm = normalize_ner_name(name)
            name2type[norm] = etype
            norm2name.setdefault(norm, name)

        seed = [
            EntityMention(text=name, label=_TYPE_TO_LABEL.get(etype, "other"), source="doktok_ner")
            for name, etype in entity_list
        ]
        result = self._enricher.enrich(text[: self._max_chars], entities=seed)

        out: list[ExtractedRelation] = []
        seen: set[tuple[str, str, str]] = set()
        for rel in result.relations:
            predicate = self._surface_to_pred.get(normalize_label(rel.predicate))
            if predicate is None:
                continue
            s_norm = normalize_ner_name(rel.subject.resolved_name())
            o_norm = normalize_ner_name(rel.object.resolved_name())
            s_type, o_type = name2type.get(s_norm), name2type.get(o_norm)
            if s_type is None or o_type is None:
                continue  # both endpoints must be grounded document entities

            pairs = PREDICATE_TYPE_PAIRS.get(predicate, [])
            if (s_type, o_type) in pairs:
                subj, obj, subj_type, obj_type = (
                    norm2name[s_norm],
                    norm2name[o_norm],
                    s_type,
                    o_type,
                )
            elif (o_type, s_type) in pairs:
                # the model emitted the triple reversed for this directed predicate; swap endpoints
                subj, obj, subj_type, obj_type = (
                    norm2name[o_norm],
                    norm2name[s_norm],
                    o_type,
                    s_type,
                )
            else:
                continue  # type pair not allowed for this predicate

            key = (subj.casefold(), predicate, obj.casefold())
            if subj.casefold() == obj.casefold() or key in seen:
                continue
            seen.add(key)
            out.append(
                ExtractedRelation(
                    subject=subj,
                    predicate=predicate,
                    object=obj,
                    subject_type=subj_type,
                    object_type=obj_type,
                    evidence=(rel.evidence_text or "")[:250],
                )
            )
        return out
