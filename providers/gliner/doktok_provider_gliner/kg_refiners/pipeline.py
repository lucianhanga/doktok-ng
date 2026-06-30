"""Composable KAG / KG enrichment pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .config import KAGEnrichmentConfig
from .entity_linker import EntityLinker
from .extractors import BaseKGExtractor, GLiNERRelexExtractor, RuleRelationExtractor
from .fallback import FallbackAdapter
from .schemas import (
    EnrichmentResult,
    EntityMention,
    KGTriple,
    LowConfidenceItem,
    RelationMention,
    TextChunk,
    ensure_text_chunk,
)
from .triple_refiner import TripleRefiner
from .utils import dedupe_entities, dedupe_relations, repair_entity


class KAGEnricher:
    """End-to-end data enrichment for KAG/KG construction.

    Default flow:
        GLiNER-Relex candidate extraction
        -> entity span repair / dedupe
        -> entity linking / canonical IDs
        -> rule-based relation augmentation
        -> relation thresholds + schema validation
        -> optional LLM fallback for uncertain cases
        -> graph-ready triples with provenance
    """

    def __init__(
        self,
        config: KAGEnrichmentConfig | None = None,
        *,
        extractor: BaseKGExtractor | None = None,
        rule_extractor: RuleRelationExtractor | None = None,
        entity_linker: EntityLinker | None = None,
        triple_refiner: TripleRefiner | None = None,
    ):
        self.config = config or KAGEnrichmentConfig()
        self.extractor = extractor or GLiNERRelexExtractor(self.config.relex_model)
        self.rule_extractor = rule_extractor or RuleRelationExtractor(self.config.rule_relations)
        self.entity_linker = entity_linker or EntityLinker(self.config.entity_linking)
        self.triple_refiner = triple_refiner or TripleRefiner(self.config.relation_refinement)
        self.fallback_adapter = FallbackAdapter()

    def enrich(
        self,
        text: str | TextChunk,
        *,
        entity_labels: Sequence[str] | dict[str, str] | None = None,
        relation_labels: Sequence[str] | dict[str, str] | None = None,
        source_doc_id: str | None = None,
        source_chunk_id: str | None = None,
        entities: Sequence[EntityMention] | None = None,
        llm_fallback=None,
        context: dict[str, Any] | None = None,
        run_llm_fallback: bool | None = None,
        **extract_kwargs: Any,
    ) -> EnrichmentResult:
        chunk = ensure_text_chunk(
            text, source_doc_id=source_doc_id, source_chunk_id=source_chunk_id
        )
        entity_labels = (
            entity_labels if entity_labels is not None else self.config.effective_entity_labels()
        )
        relation_labels = (
            relation_labels if relation_labels is not None else self.config.relation_labels
        )
        diagnostics: dict[str, Any] = {
            "source_doc_id": chunk.source_doc_id,
            "source_chunk_id": chunk.source_chunk_id,
            "extractor": self.extractor.__class__.__name__,
        }

        # 1) GLiNER-Relex extraction or injected extractor.
        extracted_entities, model_relations = self.extractor.extract(
            chunk,
            entity_labels,
            relation_labels,
            entities=entities,
            **extract_kwargs,
        )
        all_entities = [*(entities or []), *extracted_entities]
        all_entities = [repair_entity(entity, chunk.text) for entity in all_entities]
        all_entities = dedupe_entities(
            all_entities, prefer_longer=self.config.relation_refinement.prefer_longer_entities
        )

        # 2) Entity linking and canonical IDs.
        linked_entities = self.entity_linker.link(all_entities)

        # 3) Update relation endpoints to linked entities by matching span/text.
        model_relations = self._relink_relations(model_relations, linked_entities)

        # 4) Rule relation augmentation on refined entities.
        _, rule_relations = self.rule_extractor.extract(
            chunk,
            entity_labels,
            relation_labels,
            entities=linked_entities,
        )
        all_relations = dedupe_relations([*model_relations, *rule_relations])

        # 5) Refine into triples and collect low-confidence candidates.
        triples, low = self.triple_refiner.refine(
            all_relations,
            text=chunk,
            source_doc_id=chunk.source_doc_id,
            source_chunk_id=chunk.source_chunk_id,
        )

        # 6) Optional LLM fallback. User plugs runtime callable; no provider is hard-coded.
        should_fallback = self._should_run_fallback(run_llm_fallback, llm_fallback, triples, low)
        fallback_relations: list[RelationMention] = []
        fallback_triples: list[KGTriple] = []
        if should_fallback and llm_fallback is not None:
            fallback_low = low[: self.config.max_fallback_items]
            fallback_relations, fallback_triples = self.fallback_adapter.run(
                llm_fallback,
                text=chunk,
                entity_labels=entity_labels,
                relation_labels=relation_labels,
                entities=linked_entities,
                low_confidence=fallback_low,
                context=context,
            )
            fallback_relations = self._relink_relations(fallback_relations, linked_entities)
            if fallback_relations:
                extra_triples, extra_low = self.triple_refiner.refine(
                    fallback_relations,
                    text=chunk,
                    source_doc_id=chunk.source_doc_id,
                    source_chunk_id=chunk.source_chunk_id,
                )
                triples = self._dedupe_triples([*triples, *extra_triples, *fallback_triples])
                low = [*low, *extra_low]
            elif fallback_triples:
                triples = self._dedupe_triples([*triples, *fallback_triples])

        diagnostics.update(
            {
                "num_entities": len(linked_entities),
                "num_model_relations": len(model_relations),
                "num_rule_relations": len(rule_relations),
                "num_relations": len(all_relations) + len(fallback_relations),
                "num_triples": len(triples),
                "num_low_confidence": len(low),
                "llm_fallback_used": bool(should_fallback and llm_fallback is not None),
            }
        )
        return EnrichmentResult(
            entities=linked_entities,
            relations=dedupe_relations([*all_relations, *fallback_relations]),
            triples=triples,
            low_confidence=low,
            diagnostics=diagnostics,
        )

    def batch_enrich(
        self,
        chunks: Sequence[str | TextChunk],
        *,
        entity_labels: Sequence[str] | dict[str, str] | None = None,
        relation_labels: Sequence[str] | dict[str, str] | None = None,
        llm_fallback=None,
        context: dict[str, Any] | None = None,
        run_llm_fallback: bool | None = None,
        **kwargs: Any,
    ) -> list[EnrichmentResult]:
        return [
            self.enrich(
                chunk,
                entity_labels=entity_labels,
                relation_labels=relation_labels,
                llm_fallback=llm_fallback,
                context=context,
                run_llm_fallback=run_llm_fallback,
                **kwargs,
            )
            for chunk in chunks
        ]

    def _should_run_fallback(
        self,
        explicit: bool | None,
        llm_fallback,
        triples: Sequence[KGTriple],
        low: Sequence[LowConfidenceItem],
    ) -> bool:
        if explicit is not None:
            return bool(explicit and llm_fallback is not None)
        if not self.config.enable_llm_fallback or llm_fallback is None:
            return False
        if self.config.fallback_when_no_relations and not triples:
            return True
        return bool(self.config.fallback_for_low_confidence and low)

    @staticmethod
    def _relink_relations(
        relations: Sequence[RelationMention], entities: Sequence[EntityMention]
    ) -> list[RelationMention]:
        def find_match(target: EntityMention) -> EntityMention:
            for ent in entities:
                if target.start is not None and ent.start == target.start and ent.end == target.end:
                    return ent
                if target.text.lower() == ent.text.lower() and target.label == ent.label:
                    return ent
            return target

        linked: list[RelationMention] = []
        for rel in relations:
            rel.subject = find_match(rel.subject)
            rel.object = find_match(rel.object)
            linked.append(rel)
        return linked

    @staticmethod
    def _dedupe_triples(triples: Sequence[KGTriple]) -> list[KGTriple]:
        best: dict[tuple[str, str, str], KGTriple] = {}
        for triple in triples:
            current = best.get(triple.key())
            if current is None or triple.confidence > current.confidence:
                best[triple.key()] = triple
        return sorted(
            best.values(), key=lambda t: (-t.confidence, t.predicate, t.subject_name, t.object_name)
        )


__all__ = ["KAGEnricher"]
