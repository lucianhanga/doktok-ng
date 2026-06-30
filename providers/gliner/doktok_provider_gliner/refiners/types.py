from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Entity:
    """A normalized entity returned by the refinement pipeline."""

    text: str
    label: str
    start: int
    end: int
    score: float = 1.0
    source: str = "model"
    normalized: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExtractionResult:
    """Full extraction output including final and low-confidence candidates."""

    entities: list[Entity]
    low_confidence: list[Entity] = field(default_factory=list)
    raw_model_entities: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [entity.to_dict() for entity in self.entities],
            "low_confidence": [entity.to_dict() for entity in self.low_confidence],
            "raw_model_entities": self.raw_model_entities,
        }
