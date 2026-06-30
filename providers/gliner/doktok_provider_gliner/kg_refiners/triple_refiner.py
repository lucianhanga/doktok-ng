"""Relation filtering and conversion to graph-ready triples."""

from __future__ import annotations

from collections.abc import Sequence

from .config import RelationRefinementConfig
from .schemas import (
    KGTriple,
    LowConfidenceItem,
    RelationMention,
    TextChunk,
    ensure_text_chunk,
    normalize_label,
)
from .utils import dedupe_relations, evidence_window


class TripleRefiner:
    """Validate relation mentions and convert them into canonical KG triples."""

    def __init__(self, config: RelationRefinementConfig | None = None):
        self.config = config or RelationRefinementConfig()

    def refine(
        self,
        relations: Sequence[RelationMention],
        *,
        text: str | TextChunk | None = None,
        source_doc_id: str | None = None,
        source_chunk_id: str | None = None,
    ) -> tuple[list[KGTriple], list[LowConfidenceItem]]:
        chunk = ensure_text_chunk(
            text or "", source_doc_id=source_doc_id, source_chunk_id=source_chunk_id
        )
        triples: list[KGTriple] = []
        low: list[LowConfidenceItem] = []

        filtered_relations = (
            dedupe_relations(relations) if self.config.deduplicate else list(relations)
        )
        for rel in filtered_relations:
            predicate = (
                normalize_label(rel.predicate)
                if self.config.normalize_predicates
                else rel.predicate
            )
            rel.predicate = predicate
            threshold = self.config.threshold_for(predicate)
            schema = self.config.schema_for(predicate)

            reason: str | None = None
            if (
                self.config.require_distinct_entities
                and rel.subject.resolved_id() == rel.object.resolved_id()
            ):
                reason = "subject and object resolve to the same entity"
            elif schema is not None and not schema.allows(rel):
                reason = "relation violates configured subject/object schema"
            elif rel.score < threshold:
                reason = f"score below relation threshold {threshold:.2f}"

            if reason is not None:
                if self.config.keep_low_confidence:
                    low.append(
                        LowConfidenceItem(
                            kind="relation",
                            score=rel.score,
                            reason=reason,
                            payload=rel.to_dict(),
                        )
                    )
                continue

            if rel.score < self.config.low_confidence_threshold and self.config.keep_low_confidence:
                low.append(
                    LowConfidenceItem(
                        kind="relation",
                        score=rel.score,
                        reason=f"below threshold {self.config.low_confidence_threshold:.2f}",
                        payload=rel.to_dict(),
                    )
                )

            evidence = rel.evidence_text
            if not evidence and chunk.text:
                evidence = evidence_window(
                    chunk.text, rel.subject, rel.object, self.config.evidence_window_chars
                )
            triples.append(
                KGTriple(
                    subject_id=rel.subject.resolved_id(),
                    subject_name=rel.subject.resolved_name(),
                    subject_label=normalize_label(rel.subject.label),
                    predicate=predicate,
                    object_id=rel.object.resolved_id(),
                    object_name=rel.object.resolved_name(),
                    object_label=normalize_label(rel.object.label),
                    confidence=float(rel.score),
                    evidence_text=evidence,
                    source_doc_id=source_doc_id or chunk.source_doc_id,
                    source_chunk_id=source_chunk_id or chunk.source_chunk_id,
                    subject_span=(rel.subject.start, rel.subject.end),
                    object_span=(rel.object.start, rel.object.end),
                    qualifiers=dict(rel.qualifiers),
                    provenance={
                        "relation_source": rel.source,
                        "subject_source": rel.subject.source,
                        "object_source": rel.object.source,
                    },
                    metadata=dict(rel.metadata),
                )
            )

        if self.config.deduplicate:
            triples = self._dedupe_triples(triples)
        return triples, low

    @staticmethod
    def _dedupe_triples(triples: Sequence[KGTriple]) -> list[KGTriple]:
        best: dict[tuple[str, str, str], KGTriple] = {}
        for triple in triples:
            key = triple.key()
            cur = best.get(key)
            if cur is None or triple.confidence > cur.confidence:
                best[key] = triple
        return sorted(
            best.values(), key=lambda t: (-t.confidence, t.predicate, t.subject_name, t.object_name)
        )


__all__ = ["TripleRefiner"]
