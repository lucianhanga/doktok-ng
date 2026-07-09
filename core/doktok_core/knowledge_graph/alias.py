"""Conservative containment-based entity alias folding (KAG alias-resolution tier, v1).

Folds surface variants of the same real-world entity into one canonical node, e.g.
``'M-net' -> 'M-net Telekommunikations GmbH'`` or
``'Finanzamt' / 'Finanzamt München' -> 'Finanzamt München für Körperschaften'``. This improves
KAG/RAG retrieval: one node per real-world entity instead of fragmented aliases.

Signal (v1, NO embeddings): token-PREFIX containment within the SAME ``entity_type``. Node A folds
into node C iff A's normalized token sequence is a contiguous PREFIX of C's tokens and C is the
**unique longest** such superset. Two design points decide the edge cases:

  * Multi-level chains collapse to the terminal. 'Finanzamt' has two supersets ('Finanzamt München'
    and 'Finanzamt München für Körperschaften'); the unique LONGEST is the latter, so 'Finanzamt'
    folds straight into it (and 'Finanzamt München' folds into it too) -> one canonical node.
  * Divergence at the longest length is ambiguous -> SKIP. If A is a prefix of two or more supersets
    that TIE at the maximum token length (e.g. 'Bank of America' and 'Bank of England'), there is no
    unique longest, so A is left alone. (A superset that is strictly longer than the rest always
    wins; this is why 'M-net' folds into 'M-net Telekommunikations GmbH' even though 'M-net
    Internet' also starts with 'M-net' - 'M-net Internet' is shorter, and is itself NOT a prefix of
    the GmbH node, so it stays a separate node.)

Folds never cross ``entity_type``. A min-token / min-length guard stops trivial/generic prefixes
from folding. The embedding tier (``resolve.FUZZY_RESOLUTION_ENABLED``) remains deferred as the
future second signal; this containment tier is the conservative first signal.
"""

from __future__ import annotations

import logging

from doktok_contracts.ports import KnowledgeGraphRepository
from doktok_contracts.schemas import AliasFold, KgEntity

from doktok_core.knowledge_graph.entity_resolution import is_ordinal_token

logger = logging.getLogger("doktok.kag.alias")

# Guards against folding trivial/generic prefixes. An alias must have at least this many characters
# and tokens to be eligible; the genuine cases ('M-net' = 5 chars, 'Finanzamt' = 9 chars) clear it.
MIN_ALIAS_CHARS = 3
MIN_ALIAS_TOKENS = 1


def alias_tokens(normalized_value: str) -> tuple[str, ...]:
    """Tokenize a node's normalized value for prefix comparison: lowercased, whitespace-split.

    Internal punctuation (e.g. the hyphen in 'M-net') is preserved so 'M-net' is a single token that
    prefixes 'M-net Telekommunikations GmbH'.
    """
    return tuple(normalized_value.lower().split())


def compute_alias_folds(entities: list[KgEntity]) -> list[AliasFold]:
    """Pure: decide which nodes fold into which, by unique-longest token-prefix per type.

    Deterministic and side-effect free, so it is trivially unit-testable and the same inputs give
    the same folds. Returns one ``AliasFold`` per node that has a unique-longest same-type
    superset; nodes that are ambiguous, generic, or have no superset are omitted (left as-is).
    """
    by_type: dict[str, list[KgEntity]] = {}
    for entity in entities:
        by_type.setdefault(entity.entity_type.value, []).append(entity)

    folds: list[AliasFold] = []
    for etype, nodes in by_type.items():
        toks = {n.id: alias_tokens(n.normalized_value) for n in nodes}
        for alias in nodes:
            a_tok = toks[alias.id]
            if (
                len(a_tok) < MIN_ALIAS_TOKENS
                or len(alias.normalized_value.strip()) < MIN_ALIAS_CHARS
            ):
                continue
            # Supersets: same-type nodes whose tokens have A's tokens as a strict contiguous prefix.
            # A superset whose first EXTRA token is an ordinal/numeral is a distinct entity, not a
            # variant ("München" vs "München II", "Henry" vs "Henry VIII") - exclude it (#563).
            supersets = [
                c
                for c in nodes
                if c.id != alias.id
                and len(toks[c.id]) > len(a_tok)
                and toks[c.id][: len(a_tok)] == a_tok
                and not is_ordinal_token(toks[c.id][len(a_tok)])
            ]
            if not supersets:
                continue
            max_len = max(len(toks[c.id]) for c in supersets)
            longest = [c for c in supersets if len(toks[c.id]) == max_len]
            if len(longest) != 1:
                continue  # ambiguous: two or more supersets tie at the longest length -> skip
            target = longest[0]
            folds.append(
                AliasFold(
                    alias_id=alias.id,
                    alias_type=etype,
                    alias_normalized=alias.normalized_value,
                    canonical_id=target.id,
                )
            )
    return folds


def resolve_tenant_aliases(kg_repo: KnowledgeGraphRepository, tenant_id: str) -> int:
    """Run the alias-folding pass for one tenant; returns how many nodes were folded.

    Cross-document and tenant-level (NOT a per-document feature): it reads every node, computes the
    containment folds, and applies them transactionally via the repository (which serializes the
    apply under a per-tenant advisory lock). Idempotent: a second run finds the folded nodes already
    gone and is a no-op.
    """
    entities = kg_repo.list_entities(tenant_id)
    folds = compute_alias_folds(entities)
    if not folds:
        return 0
    merged = kg_repo.resolve_aliases(tenant_id, folds)
    if merged:
        logger.info("kag alias pass: folded %d entity alias(es) for tenant %s", merged, tenant_id)
    return merged
