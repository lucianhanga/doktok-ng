"""Deterministic entity-resolution matching cascade (#508 Wave 1 / P0, #533 + #534 P1).

Decides which same-type canonical nodes look like surface variants of one real-world entity
("lucian hanga" / "lucianhanga" / "hanja lucian"). Four stages, all deterministic - no
embeddings, no LLM:

  1. ``token_set``    - identical token-sort keys (``normalize_entity_name``). At write time this
                        stage is FREE: the node id derives from the sort key, so word-order and
                        punctuation variants collapse into one node before matching ever runs. It
                        still fires here for nodes minted before #508 (pre-sort-key ids).
  2. ``token_subset`` - one name's token set is a PROPER subset of the other's, all shared tokens
                        exact, and the smaller name has >= 2 tokens ("lucian hanga" is a variant
                        of "lucian cosmin hanga"; a bare "hanga" matches nothing). #533.
  3. ``token_typo``   - token sets align 1:1 with exactly ONE non-exact pair, and that pair is a
                        single-character typo (both tokens len >= 4, Damerau-Levenshtein <= 1,
                        SAME first character - the guard that keeps "gruber"/"huber" apart while
                        catching the OCR-ish "hanja"/"hanga"). #534.
  4. ``fuzzy_trgm``   - trigram similarity at/above ``SUGGESTION_THRESHOLD``. The pure-Python
                        implementation mirrors pg_trgm semantics (per-word padded trigram sets, so
                        similarity is word-order-insensitive) - the in-memory repository and the
                        evaluation harness score exactly like the Postgres ``similarity()`` tier.

Only ``token_set`` is a CERTAIN match: the adjudication layer (#510) passes it through without an
LLM call and routes every other method - including ``token_subset`` and ``token_typo`` - to the
LLM adjudicator. Nothing here auto-merges.

Structured as an ordered stage cascade (first stage to fire labels the pair) so P1+ signals -
embedding cosine, context disambiguation - slot in as extra stages without reworking callers. The
cascade only PROPOSES merges (``KgMergeSuggestion``); applying one is a separate, logged,
reversible step (``KnowledgeGraphRepository.merge_entities``). Blocking is by ``entity_type``
plus a shared-trigram inverted index (the in-memory analogue of the GIN ``gin_trgm_ops`` index),
never a full O(n^2) pair scan.

Known P0 limitation (by design): two DIFFERENT real-world entities with the same or near-identical
name cannot be separated by any name-only signal; disambiguation by context/embeddings is P1+.
The suggestion threshold is tuned on the golden set in ``core/tests/test_entity_resolution_eval``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from doktok_contracts.schemas import KgEntity, KgMergeSuggestion

from doktok_core.entities.ner import normalize_entity_name

# Cascade method labels (also the `source` values persisted in kg_entity_aliases / merge log;
# both columns are free text since 0041, so new labels need no migration).
METHOD_TOKEN_SET = "token_set"
METHOD_TOKEN_SUBSET = "token_subset"
METHOD_TOKEN_TYPO = "token_typo"
METHOD_FUZZY_TRGM = "fuzzy_trgm"

# Fixed suggestion scores for the deterministic name-structure stages. Chosen to slot between
# token_set certainty (1.0) and the fuzzy tier (<= ~0.67 on the golden set) so the review queue
# sorts subset > typo > trigram; neither is 1.0 because neither is certain - both are
# LLM-adjudicated suggestions, never auto-merges.
TOKEN_SUBSET_SCORE = 0.85
TOKEN_TYPO_SCORE = 0.75

# A typo-pair token must be at least this long: short tokens ("jo"/"ja", initials) are one edit
# away from too many unrelated tokens for a DL<=1 signal to mean anything.
_TYPO_MIN_TOKEN_LEN = 4

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


def _name_tokens(value: str) -> frozenset[str]:
    """The name's token set under the same normalization as the sort key (``casefold``,
    punctuation stripped, deduped) - the shared vocabulary of the token-structure stages."""
    return frozenset(normalize_entity_name(value).split())


def _within_one_edit(a: str, b: str) -> bool:
    """Damerau-Levenshtein distance <= 1, computed directly (no DP table needed at cap 1):
    equal strings, one substitution, one ADJACENT transposition, or one insertion/deletion."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diffs = [i for i, (ca, cb) in enumerate(zip(a, b, strict=True)) if ca != cb]
        if len(diffs) == 1:
            return True  # one substitution
        return (  # one adjacent transposition ("lucain" ~ "lucian")
            len(diffs) == 2
            and diffs[1] == diffs[0] + 1
            and a[diffs[0]] == b[diffs[1]]
            and a[diffs[1]] == b[diffs[0]]
        )
    shorter, longer = (a, b) if la < lb else (b, a)
    prefix = 0
    while prefix < len(shorter) and shorter[prefix] == longer[prefix]:
        prefix += 1
    return shorter[prefix:] == longer[prefix + 1 :]  # one insertion/deletion


def is_typo_token_pair(a: str, b: str) -> bool:
    """True when two DIFFERENT tokens look like a single-character typo of one name token.

    Guards (#534, precision-first):
    - both tokens ``len >= 4`` - short tokens are one edit from too many unrelated tokens;
    - SAME FIRST CHARACTER - load-bearing: "gruber"/"huber" differ in the leading char and must
      stay apart, while OCR errors ("hanja" ~ "hanga") rarely corrupt the leading char;
    - Damerau-Levenshtein distance <= 1.
    """
    if a == b:
        return False
    if min(len(a), len(b)) < _TYPO_MIN_TOKEN_LEN:
        return False
    if a[0] != b[0]:
        return False
    return _within_one_edit(a, b)


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
class TokenSubsetStage:
    """Stage 2 (#533): one token set is a PROPER subset of the other, all shared tokens EXACT.

    "lucian hanga" is proposed as a variant of "lucian cosmin hanga" (the shorter name folds
    into the longer via ``canonical_preference``). Guardrail: the SMALLER name must have >= 2
    tokens - a bare "hanga" carries too little identity and matches nothing here. Different
    given names are excluded by construction ({lucian, hanga} is not a subset of
    {daniel, hanga}). NOT a certain match: the suggestion is LLM-adjudicated, never auto-merged
    ("lucian hanga" could still be a different person than "lucian cosmin hanga").
    """

    name: str = METHOD_TOKEN_SUBSET

    def score(self, a_value: str, b_value: str) -> float | None:
        ta, tb = _name_tokens(a_value), _name_tokens(b_value)
        smaller, larger = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        if len(smaller) >= 2 and smaller < larger:  # proper subset, no fuzz in this stage
            return TOKEN_SUBSET_SCORE
        return None


@dataclass(frozen=True)
class TokenTypoStage:
    """Stage 3 (#534): token sets align 1:1 with exactly ONE single-character-typo pair.

    Exact tokens pair off first; the leftovers must be exactly one token per side, and that pair
    must satisfy ``is_typo_token_pair`` (len >= 4, DL <= 1, same first character). Catches the
    OCR-ish "hanja lucian" ~ "lucian hanga" (token-sorted: hanja~hanga, exact lucian) while
    "hans gruber" ~ "hans huber" stays apart (leading char differs). Guardrail: both names need
    >= 2 tokens - single-token names ("meier"/"meyer") are too ambiguous for a typo-only signal.
    LLM-adjudicated, never auto-merged. Initial-vs-full-token matching ("l. hanga") is
    deliberately NOT attempted - too over-merge-prone for a deterministic stage.
    """

    name: str = METHOD_TOKEN_TYPO

    def score(self, a_value: str, b_value: str) -> float | None:
        ta, tb = _name_tokens(a_value), _name_tokens(b_value)
        if len(ta) != len(tb) or len(ta) < 2:
            return None
        rest_a, rest_b = ta - tb, tb - ta  # equal sizes: the leftovers are the same count
        if len(rest_a) != 1:
            return None  # 0 leftovers is token_set's certain tier; >1 exceeds the typo budget
        if is_typo_token_pair(next(iter(rest_a)), next(iter(rest_b))):
            return TOKEN_TYPO_SCORE
        return None


@dataclass(frozen=True)
class TrigramStage:
    """Stage 4: word-order-insensitive trigram similarity at/above the threshold."""

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
        default_factory=lambda: (
            TokenSetStage(),
            TokenSubsetStage(),
            TokenTypoStage(),
            TrigramStage(),
        )
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
