"""Shared schemas for KAG / knowledge-graph enrichment.

The package intentionally uses dataclasses instead of pydantic so it can be
embedded into existing pipelines without adding mandatory dependencies.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

JsonDict = dict[str, Any]


@dataclass(slots=True)
class TextChunk:
    """A piece of source text that should be enriched into KG facts."""

    text: str
    source_doc_id: str | None = None
    source_chunk_id: str | None = None
    metadata: JsonDict = field(default_factory=dict)

    def chunk_id(self) -> str:
        return self.source_chunk_id or self.source_doc_id or "chunk"


@dataclass(slots=True)
class EntityMention:
    """A detected entity span before or after entity linking."""

    text: str
    label: str
    start: int | None = None
    end: int | None = None
    score: float = 1.0
    source: str = "unknown"
    canonical_id: str | None = None
    canonical_name: str | None = None
    aliases: list[str] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    def resolved_name(self) -> str:
        return self.canonical_name or self.text

    def resolved_id(self) -> str:
        if self.canonical_id:
            return self.canonical_id
        return f"{self.label}:{slugify(self.resolved_name())}"

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(slots=True)
class RelationMention:
    """A relation between two entity mentions as extracted from text."""

    subject: EntityMention
    predicate: str
    object: EntityMention
    score: float = 1.0
    evidence_text: str | None = None
    source: str = "unknown"
    qualifiers: JsonDict = field(default_factory=dict)
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        data = asdict(self)
        return data


@dataclass(slots=True)
class KGTriple:
    """Canonical graph-ready triple with provenance."""

    subject_id: str
    subject_name: str
    subject_label: str
    predicate: str
    object_id: str
    object_name: str
    object_label: str
    confidence: float
    evidence_text: str | None = None
    source_doc_id: str | None = None
    source_chunk_id: str | None = None
    subject_span: tuple[int | None, int | None] | None = None
    object_span: tuple[int | None, int | None] | None = None
    qualifiers: JsonDict = field(default_factory=dict)
    provenance: JsonDict = field(default_factory=dict)
    metadata: JsonDict = field(default_factory=dict)

    def key(self) -> tuple[str, str, str]:
        return (self.subject_id, self.predicate, self.object_id)

    def to_dict(self) -> JsonDict:
        return asdict(self)

    def to_cypher_params(self) -> JsonDict:
        """Return parameters suitable for a parameterized Neo4j MERGE query."""
        return self.to_dict()


@dataclass(slots=True)
class LowConfidenceItem:
    """Candidate that can be reviewed by a human or LLM fallback."""

    kind: str
    score: float
    reason: str
    payload: JsonDict

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(slots=True)
class EnrichmentResult:
    """Full output of a KAG enrichment pass."""

    entities: list[EntityMention] = field(default_factory=list)
    relations: list[RelationMention] = field(default_factory=list)
    triples: list[KGTriple] = field(default_factory=list)
    low_confidence: list[LowConfidenceItem] = field(default_factory=list)
    diagnostics: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "relations": [r.to_dict() for r in self.relations],
            "triples": [t.to_dict() for t in self.triples],
            "low_confidence": [i.to_dict() for i in self.low_confidence],
            "diagnostics": self.diagnostics,
        }


@dataclass(slots=True)
class RelationSchemaRule:
    """Allowed domain/range for a relation predicate.

    Example:
        RelationSchemaRule(
            predicate="acquired",
            subject_labels=["organization"],
            object_labels=["organization"],
        )
    """

    predicate: str
    subject_labels: list[str] = field(default_factory=list)
    object_labels: list[str] = field(default_factory=list)
    inverse_predicate: str | None = None
    min_confidence: float | None = None

    def allows(self, relation: RelationMention) -> bool:
        subj_ok = not self.subject_labels or normalize_label(relation.subject.label) in {
            normalize_label(x) for x in self.subject_labels
        }
        obj_ok = not self.object_labels or normalize_label(relation.object.label) in {
            normalize_label(x) for x in self.object_labels
        }
        return subj_ok and obj_ok


# Callable signatures for runtime pluggable LLM fallback.
# A fallback receives text, canonical entities, target relation labels, and low-confidence items.
# It should return RelationMention objects, KGTriple objects, dictionaries, or a mix.
LLMFallback = Callable[..., Sequence[RelationMention | KGTriple | JsonDict]]


def normalize_label(label: str) -> str:
    return " ".join(str(label).replace("_", " ").replace("-", " ").lower().split())


def slugify(value: str) -> str:
    cleaned = []
    last_dash = False
    for ch in str(value).strip().lower():
        if ch.isalnum():
            cleaned.append(ch)
            last_dash = False
        elif not last_dash:
            cleaned.append("_")
            last_dash = True
    return "".join(cleaned).strip("_") or "entity"


def ensure_text_chunk(
    value: str | TextChunk, *, source_doc_id: str | None = None, source_chunk_id: str | None = None
) -> TextChunk:
    if isinstance(value, TextChunk):
        return value
    return TextChunk(text=value, source_doc_id=source_doc_id, source_chunk_id=source_chunk_id)


__all__ = [
    "JsonDict",
    "TextChunk",
    "EntityMention",
    "RelationMention",
    "KGTriple",
    "LowConfidenceItem",
    "EnrichmentResult",
    "RelationSchemaRule",
    "LLMFallback",
    "normalize_label",
    "slugify",
    "ensure_text_chunk",
]
