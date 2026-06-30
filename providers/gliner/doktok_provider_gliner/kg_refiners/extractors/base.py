"""Base interfaces for relation/entity extractors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from ..schemas import EntityMention, RelationMention, TextChunk


class BaseKGExtractor(ABC):
    """Extract entities and relations from text."""

    @abstractmethod
    def extract(
        self,
        text: str | TextChunk,
        entity_labels: Sequence[str] | dict[str, str],
        relation_labels: Sequence[str] | dict[str, str],
        *,
        entities: Sequence[EntityMention] | None = None,
        **kwargs,
    ) -> tuple[list[EntityMention], list[RelationMention]]:
        raise NotImplementedError


__all__ = ["BaseKGExtractor"]
