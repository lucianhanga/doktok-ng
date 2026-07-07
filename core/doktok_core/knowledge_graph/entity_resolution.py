"""Deterministic entity-resolution matching cascade (#508, Wave 1 / P0).

Decides which same-type canonical nodes look like surface variants of one real-world entity
("lucian hanga" / "lucianhanga" / "hanja lucian"). Two stages, both deterministic - no
embeddings, no LLM:

  1. ``token_set``  - identical token-sort keys (``normalize_entity_name``). At write time this
                      stage is FREE: the node id derives from the sort key, so word-order and
                      punctuation variants collapse into one node before matching ever runs. It
                      still fires here for nodes minted before #508 (pre-sort-key ids).
  2. ``fuzzy_trgm`` - trigram similarity at/above ``SUGGESTION_THRESHOLD``. The pure-Python
                      implementation mirrors pg_trgm semantics (per-word padded trigram sets, so
                      similarity is word-order-insensitive) - the in-memory repository and the
                      evaluation harness score exactly like the Postgres ``similarity()`` tier.

Structured as an ordered stage cascade (first stage to fire labels the pair) so P1+ signals -
dmetaphone, token subset/superset, embedding cosine, LLM adjudication - slot in as extra stages
without reworking callers. The cascade only PROPOSES merges (``KgMergeSuggestion``); applying one
is a separate, logged, reversible step (``KnowledgeGraphRepository.merge_entities``). Blocking is
by ``entity_type`` plus a shared-trigram inverted index (the in-memory analogue of the GIN
``gin_trgm_ops`` index), never a full O(n^2) pair scan.

Known P0 limitation (by design): two DIFFERENT real-world entities with the same or near-identical
name cannot be separated by any name-only signal; disambiguation by context/embeddings is P1+.
The suggestion threshold is tuned on the golden set in ``core/tests/test_entity_resolution_eval``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from doktok_contracts.schemas import KgEntity, KgMergeSuggestion

from doktok_core.entities.ner import normalize_entity_name

# Cascade method labels (also the `source` values persisted in kg_entity_aliases / merge log).
METHOD_TOKEN_SET = "token_set"
METHOD_FUZZY_TRGM = "fuzzy_trgm"

# The fuzzy suggestion threshold, tuned on the golden set: the true-variant pairs
# ('lucianhanga' 0.667, 'hanja lucian' 0.625, 'cosmin hanga lucian' 0.65) sit between 0.6 and 0.7,
# while the confusable-negative pair ('hans gruber' vs 'hans huber') scores ~0.53. 0.6 separates
# them. `find_similar_entities` keeps a stricter 0.7 default for point lookups.
SUGGESTION_THRESHOLD = 0.6


def trigram_set(text: str) -> frozenset[str]:
    """The pg_trgm-compatible trigram set of a string.

    Mirrors Postgres: lowercase, split into words on non-alphanumerics, pad each word with two
    leading and one trailing space, take 3-grams, union as a SET. Because trigrams are per-word,
    the set - and therefore the similarity - is word-order-insensitive.
    """
    grams: set[str] = set()
    word: list[str] = []
    for ch in text.lower() + " ":  # trailing sentinel flushes the last word
        if ch.isalnum():
            word.append(ch)
            continue
        if word:
            padded = "  " + "".join(word) + " "
            grams.update(padded[i : i + 3] for i in range(len(padded) - 2))
            word = []
    return frozenset(grams)


def trigram_similarity(a: str, b: str) -> float:
    """Jaccard similarity of the two trigram sets in ``[0, 1]`` (pg_trgm ``similarity()``)."""
    ta, tb = trigram_set(a), trigram_set(b)
    if not ta or not tb:
        return 0.0
    shared = len(ta & tb)
    union = len(ta) + len(tb) - shared
    return shared / union if union else 0.0


class MatchStage(Protocol):
    """One signal in the cascade: score a pair of normalized values, or pass (None)."""

    @property
    def name(self) -> str: ...

    def score(self, a_value: str, b_value: str) -> float | None: ...


@dataclass(frozen=True)
class TokenSetStage:
    """Stage 1: identical token-sort keys (order/punctuation variants) - a certain match."""

    name: str = METHOD_TOKEN_SET

    def score(self, a_value: str, b_value: str) -> float | None:
        if normalize_entity_name(a_value) == normalize_entity_name(b_value):
            return 1.0
        return None


@dataclass(frozen=True)
class TrigramStage:
    """Stage 2: word-order-insensitive trigram similarity at/above the threshold."""

    threshold: float = SUGGESTION_THRESHOLD
    name: str = METHOD_FUZZY_TRGM

    def score(self, a_value: str, b_value: str) -> float | None:
        similarity = trigram_similarity(a_value, b_value)
        return similarity if similarity >= self.threshold else None


def is_canonical(entity: KgEntity) -> bool:
    """A node is canonical when its ``canonical_id`` is unset or points at itself."""
    return entity.canonical_id is None or entity.canonical_id == entity.id


def canonical_preference(a: KgEntity, b: KgEntity) -> tuple[KgEntity, KgEntity]:
    """Pick ``(canonical, alias)`` for a matched pair, deterministically.

    Prefer the more informative node: more tokens in the sort key, then the longer value, then
    the smaller id (pure tie-break). Matches the alias tier's fold-into-longest philosophy
    ('lucian hanga' folds into 'lucian cosmin hanga', not the reverse).
    """

    def rank(entity: KgEntity) -> tuple[int, int]:
        key = normalize_entity_name(entity.normalized_value)
        return (len(key.split()), len(entity.normalized_value))

    if rank(a) > rank(b):
        return a, b
    if rank(b) > rank(a):
        return b, a
    return (a, b) if a.id < b.id else (b, a)


@dataclass(frozen=True)
class MatchCascade:
    """The ordered stage cascade; the FIRST stage to fire labels the pair."""

    stages: tuple[MatchStage, ...] = field(
        default_factory=lambda: (TokenSetStage(), TrigramStage())
    )

    def score_pair(self, a: KgEntity, b: KgEntity) -> tuple[str, float] | None:
        """``(method, score)`` from the first firing stage; None when no stage fires or the
        pair is not comparable (different tenant or entity type - merges never cross either)."""
        if a.tenant_id != b.tenant_id or a.entity_type != b.entity_type or a.id == b.id:
            return None
        for stage in self.stages:
            score = stage.score(a.normalized_value, b.normalized_value)
            if score is not None:
                return stage.name, score
        return None

    def propose(self, entities: list[KgEntity], *, limit: int = 50) -> list[KgMergeSuggestion]:
        """Candidate merges among CANONICAL nodes, best-scoring first, capped at ``limit``.

        Blocked by ``entity_type``, then candidate pairs come from a shared-trigram inverted
        index (only pairs with at least one common trigram are scored) - the in-memory analogue
        of the Postgres GIN-index `%` pre-filter, so this is not an O(n^2) scan.
        """
        canonicals = [e for e in entities if is_canonical(e)]
        by_gram: dict[str, list[int]] = {}
        for idx, entity in enumerate(canonicals):
            for gram in trigram_set(entity.normalized_value):
                by_gram.setdefault(gram, []).append(idx)
        candidate_pairs: set[tuple[int, int]] = set()
        for indices in by_gram.values():
            for i, left in enumerate(indices):
                for right in indices[i + 1 :]:
                    candidate_pairs.add((left, right))
        suggestions: list[KgMergeSuggestion] = []
        for left, right in candidate_pairs:
            a, b = canonicals[left], canonicals[right]
            decision = self.score_pair(a, b)
            if decision is None:
                continue
            method, score = decision
            canonical, alias = canonical_preference(a, b)
            suggestions.append(
                KgMergeSuggestion(
                    tenant_id=canonical.tenant_id,
                    entity_type=canonical.entity_type,
                    canonical_id=canonical.id,
                    canonical_value=canonical.normalized_value,
                    alias_id=alias.id,
                    alias_value=alias.normalized_value,
                    method=method,
                    score=score,
                )
            )
        suggestions.sort(key=lambda s: (-s.score, s.canonical_id, s.alias_id))
        return suggestions[:limit]
