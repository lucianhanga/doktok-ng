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

# Separator used when composing the adjudication-cache pair key. A NUL byte can never appear inside
# a normalized entity value, so it can never collide two distinct entities into one key.
_PAIR_KEY_SEP = "\x1f"


def merge_adjudication_pair_key(value_a: str, value_b: str) -> str:
    """Stable, order-independent cache key for an adjudicated entity pair (#535).

    Normalizes each value with the same token-sort normalization the KG node ids derive from
    (``normalize_entity_name``), then sorts the two normalized values so ``(a, b)`` and ``(b, a)``
    key identically. Because the normalization collapses word-order/punctuation variants and the
    node ids are re-minted from it on every KG rebuild, the key survives re-derivation: the same
    real-world pair re-adjudicates to the SAME row rather than a fresh LLM call.
    """
    na, nb = normalize_entity_name(value_a), normalize_entity_name(value_b)
    lo, hi = sorted((na, nb))
    return f"{lo}{_PAIR_KEY_SEP}{hi}"


def merge_adjudication_score_bucket(score: float) -> str:
    """Round a suggestion score to 2 decimals as a stable text bucket for the cache key (#535).

    Bucketing keeps tiny trigram-score drift (e.g. 0.691 vs 0.692) on the same cached verdict while
    a genuine tier change still re-adjudicates (the deterministic stages carry fixed scores).
    """
    return f"{score:.2f}"


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


# Ordinal/numeral tokens that DISTINGUISH otherwise-identical names: "München II" is not "München",
# "Henry VIII" is not "Henry", "Section 2" is not "Section 3". A closed roman-numeral set (i..xx),
# not a regex like ^[ivxlcdm]+$ which also matches real words ("mix", "dim", "civil"); pure-digit
# tokens are numeric by construction.
_ROMAN_NUMERALS: frozenset[str] = frozenset(
    [
        "i",
        "ii",
        "iii",
        "iv",
        "v",
        "vi",
        "vii",
        "viii",
        "ix",
        "x",
        "xi",
        "xii",
        "xiii",
        "xiv",
        "xv",
        "xvi",
        "xvii",
        "xviii",
        "xix",
        "xx",
    ]
)


def is_ordinal_token(token: str) -> bool:
    """A pure-digit or roman-numeral (i..xx) token - an ordinal that distinguishes entities (#563).

    Shared by the merge cascade (:meth:`MatchCascade.score_pair`) and the containment alias-fold
    (``alias.compute_alias_folds``) so both refuse to collapse "München" into "München II"."""
    return token.isdigit() or token in _ROMAN_NUMERALS


def differs_only_by_ordinal(a_value: str, b_value: str) -> bool:
    """True when two names share a base word and differ ONLY by ordinal/number tokens (#563).

    "münchen" vs "münchen ii", "münchen ii" vs "münchen iii", "section 2" vs "section 3": the
    differing tokens are ordinals/numerals, so the names denote DIFFERENT entities and must never be
    a merge candidate. Requires a non-empty shared base so degenerate all-numeral names ("ii" vs
    "iii") fall through to the normal stages instead.
    """
    ta, tb = _name_tokens(a_value), _name_tokens(b_value)
    diff = ta ^ tb
    if not diff or not (ta & tb):
        return False
    return all(is_ordinal_token(token) for token in diff)


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

    if _name_rank(a.normalized_value) > _name_rank(b.normalized_value):
        return a, b
    if _name_rank(b.normalized_value) > _name_rank(a.normalized_value):
        return b, a
    return (a, b) if a.id < b.id else (b, a)


def _name_rank(value: str) -> tuple[int, int]:
    """How informative a name is: token count in the sort key, then length. Higher = more complete
    ('lucian cosmin hanga' > 'lucian hanga'). The cluster representative maximizes this."""
    return (len(normalize_entity_name(value).split()), len(value))


def retarget_to_cluster_root(suggestions: list[KgMergeSuggestion]) -> list[KgMergeSuggestion]:
    """Re-point each merge suggestion at its cluster's most-complete name (transitive closure).

    The cascade proposes PAIRWISE matches, so a chain like ``hanja lucian`` -> ``lucian hanga`` ->
    ``lucian cosmin hanga`` surfaces as one-hop suggestions - the OCR variant targets its nearest
    neighbour, not the terminal canonical. This groups the connected suggestions (union-find over
    endpoint ids) and emits, per non-representative node, ONE suggestion pointing at the cluster
    representative (highest ``_name_rank`` node), so ``hanja lucian`` -> ``lucian cosmin hanga``.
    Each re-pointed suggestion keeps the node's STRONGEST original method/score (its evidence of
    belonging); the per-pair ``llm_canonical`` is cleared since direction is now cluster-derived.
    A plain two-node pair is unchanged (its representative is already the canonical side).
    """
    if not suggestions:
        return []

    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:  # path-compress
            parent[node], node = root, parent[node]
        return root

    value: dict[str, str] = {}
    strongest: dict[str, KgMergeSuggestion] = {}
    for s in suggestions:
        parent.setdefault(s.canonical_id, s.canonical_id)
        parent.setdefault(s.alias_id, s.alias_id)
        parent[find(s.alias_id)] = find(s.canonical_id)
        value[s.canonical_id] = s.canonical_value
        value[s.alias_id] = s.alias_value
        for node_id in (s.canonical_id, s.alias_id):
            if node_id not in strongest or s.score > strongest[node_id].score:
                strongest[node_id] = s

    clusters: dict[str, list[str]] = {}
    for node_id in value:
        clusters.setdefault(find(node_id), []).append(node_id)

    result: list[KgMergeSuggestion] = []
    for members in clusters.values():
        rep = min(members, key=lambda n: (-_name_rank(value[n])[0], -_name_rank(value[n])[1], n))
        for node_id in members:
            if node_id == rep:
                continue
            src = strongest[node_id]
            result.append(
                src.model_copy(
                    update={
                        "canonical_id": rep,
                        "canonical_value": value[rep],
                        "alias_id": node_id,
                        "alias_value": value[node_id],
                        "llm_canonical": None,
                    }
                )
            )
    result.sort(key=lambda s: (-s.score, s.canonical_id, s.alias_id))
    return result


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
        # Names differing only by an ordinal/numeral are distinct entities ("München II" vs
        # "München"), even when trigram-similar - block before any stage fires (#563).
        if differs_only_by_ordinal(a.normalized_value, b.normalized_value):
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
