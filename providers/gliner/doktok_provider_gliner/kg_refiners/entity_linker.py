"""Entity linking and canonicalization for graph nodes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher

from .config import EntityLinkingConfig
from .schemas import EntityMention, normalize_label, slugify
from .utils import normalize_text

try:  # optional dependency
    from rapidfuzz import fuzz  # type: ignore
except Exception:  # pragma: no cover - optional import
    fuzz = None


@dataclass(slots=True)
class CanonicalEntity:
    entity_id: str
    canonical_name: str
    label: str
    aliases: list[str]


class EntityLinker:
    """Resolve entity mentions into stable graph node IDs.

    The linker uses user-supplied gazetteers and aliases first, then falls back
    to deterministic slug IDs. If rapidfuzz is installed, fuzzy alias matching is
    used; otherwise Python's SequenceMatcher is used.
    """

    def __init__(self, config: EntityLinkingConfig | None = None):
        self.config = config or EntityLinkingConfig()
        self.index: dict[str, list[CanonicalEntity]] = {}
        self._build_index()

    def _build_index(self) -> None:
        self.index = {}
        for label, raw_entries in self.config.gazetteers.items():
            label_norm = normalize_label(label)
            entries: list[CanonicalEntity] = []
            if isinstance(raw_entries, Mapping):
                iterable = raw_entries.items()
            else:
                iterable = [(str(name), []) for name in raw_entries]
            for canonical_name, aliases in iterable:
                alias_list = list(aliases or [])
                prefix = self.config.id_prefix_by_label.get(label_norm, label_norm)
                entity_id = f"{prefix}:{slugify(canonical_name)}"
                entries.append(
                    CanonicalEntity(
                        entity_id=entity_id,
                        canonical_name=str(canonical_name),
                        label=label_norm,
                        aliases=[str(canonical_name), *map(str, alias_list)],
                    )
                )
            self.index.setdefault(label_norm, []).extend(entries)

    def link(self, entities: Iterable[EntityMention]) -> list[EntityMention]:
        return [self.link_one(entity) for entity in entities]

    def link_one(self, entity: EntityMention) -> EntityMention:
        entity.label = normalize_label(entity.label)
        candidates = self.index.get(entity.label, [])
        if not candidates:
            entity.canonical_name = entity.canonical_name or normalize_text(entity.text)
            entity.canonical_id = entity.canonical_id or self._default_id(entity)
            return entity

        best: tuple[float, CanonicalEntity] | None = None
        for candidate in candidates:
            for alias in candidate.aliases:
                score = self._match_score(entity.text, alias)
                if best is None or score > best[0]:
                    best = (score, candidate)

        if best and best[0] >= self.config.fuzzy_threshold:
            _, candidate = best
            entity.canonical_id = candidate.entity_id
            entity.canonical_name = candidate.canonical_name
            entity.aliases = sorted({*entity.aliases, *candidate.aliases})
            entity.metadata["entity_link_score"] = best[0]
            entity.metadata["entity_link_method"] = (
                "gazetteer_fuzzy" if best[0] < 1 else "gazetteer_exact"
            )
        else:
            entity.canonical_name = entity.canonical_name or normalize_text(entity.text)
            entity.canonical_id = entity.canonical_id or self._default_id(entity)
            entity.metadata.setdefault("entity_link_method", "deterministic_slug")
        return entity

    def _default_id(self, entity: EntityMention) -> str:
        prefix = self.config.id_prefix_by_label.get(entity.label, entity.label)
        return f"{prefix}:{slugify(entity.canonical_name or entity.text)}"

    def _match_score(self, left: str, right: str) -> float:
        if not self.config.case_sensitive:
            left = left.lower()
            right = right.lower()
        left = normalize_text(left)
        right = normalize_text(right)
        if left == right:
            return 1.0
        if not self.config.use_fuzzy:
            return 0.0
        if fuzz is not None:  # pragma: no cover - depends on optional dependency
            return fuzz.token_sort_ratio(left, right) / 100.0
        return SequenceMatcher(None, left, right).ratio()


__all__ = ["CanonicalEntity", "EntityLinker"]
