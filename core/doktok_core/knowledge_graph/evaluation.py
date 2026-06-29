"""Deterministic knowledge-graph quality metrics (KAG eval, graph-quality tracks).

Pure metric logic so it is CI-testable with synthetic inputs and reusable by the local runner (which
feeds it the real extracted graph). Two tracks, both measuring the GRAPH itself (the relation
extractor + edge evidence), complementing the relational track that measures end-to-end answers:

  * Edge precision / recall / F1 - did the extractor produce the right relationship triples? Matched
    on the normalized ``(subject, predicate, object)`` key (``normalize_ner_name`` for endpoints,
    upper-cased predicate). Computed overall AND per-predicate, with the missed-gold and spurious
    lists so a failure is diagnosable.
  * Provenance correctness - is each edge's evidence trustworthy? An edge's provenance is valid iff
    the evidence span is non-empty, is a (whitespace-normalized) substring of the cited source
    document's text, AND both endpoint surface forms appear within that span.

No DB and no model in this module - the runner does the SQL/IO and passes plain values in.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from doktok_core.entities.ner import normalize_ner_name

# ----------------------------------------------------------------------- edge precision/recall


@dataclass(frozen=True)
class EdgeTriple:
    """One directed relationship triple. ``source`` (a filename) is informational - matching is on
    the normalized ``(subject, predicate, object)`` key only, never the source."""

    subject: str
    predicate: str
    object: str
    source: str = ""


def normalized_key(triple: EdgeTriple) -> tuple[str, str, str]:
    """The match key: endpoints normalized like the graph builder (``normalize_ner_name``), the
    predicate upper-cased to the closed vocabulary's form. Type-aware via the predicate string,
    which encodes the allowed subject/object types."""
    return (
        normalize_ner_name(triple.subject),
        triple.predicate.strip().upper(),
        normalize_ner_name(triple.object),
    )


@dataclass(frozen=True)
class EdgeScore:
    """Precision/recall/F1 for a slice (overall, or one predicate)."""

    predicate: str | None  # None = overall
    true_positives: int
    gold_total: int
    extracted_total: int
    precision: float
    recall: float
    f1: float


@dataclass
class EdgeEvalReport:
    overall: EdgeScore
    per_predicate: dict[str, EdgeScore]
    missed_gold: list[EdgeTriple] = field(default_factory=list)  # gold not matched by any extracted
    spurious: list[EdgeTriple] = field(default_factory=list)  # extracted not matching any gold


def _score(predicate: str | None, tp: int, gold: int, extracted: int) -> EdgeScore:
    precision = tp / extracted if extracted else 0.0
    recall = tp / gold if gold else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return EdgeScore(
        predicate=predicate,
        true_positives=tp,
        gold_total=gold,
        extracted_total=extracted,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
    )


def score_edges(extracted: Sequence[EdgeTriple], gold: Sequence[EdgeTriple]) -> EdgeEvalReport:
    """Precision/recall/F1 of ``extracted`` edges against ``gold``, overall and per-predicate.

    Edges are de-duplicated by their normalized key before scoring (the graph stores each directed
    triple once, and a gold set must not double-count). ``missed_gold`` / ``spurious`` carry the
    original triples for diagnosis.
    """
    extracted_by_key = {normalized_key(t): t for t in extracted}
    gold_by_key = {normalized_key(t): t for t in gold}
    extracted_keys = set(extracted_by_key)
    gold_keys = set(gold_by_key)
    matched = extracted_keys & gold_keys

    overall = _score(None, len(matched), len(gold_keys), len(extracted_keys))

    predicates = sorted({k[1] for k in gold_keys | extracted_keys})
    per_predicate: dict[str, EdgeScore] = {}
    for predicate in predicates:
        g = {k for k in gold_keys if k[1] == predicate}
        e = {k for k in extracted_keys if k[1] == predicate}
        per_predicate[predicate] = _score(predicate, len(g & e), len(g), len(e))

    missed_gold = [gold_by_key[k] for k in sorted(gold_keys - extracted_keys)]
    spurious = [extracted_by_key[k] for k in sorted(extracted_keys - gold_keys)]
    return EdgeEvalReport(
        overall=overall,
        per_predicate=per_predicate,
        missed_gold=missed_gold,
        spurious=spurious,
    )


# ----------------------------------------------------------------------- provenance correctness


@dataclass(frozen=True)
class ProvenanceInput:
    """One edge's provenance row plus the text of the document it cites."""

    edge: EdgeTriple
    document_id: str
    evidence: str
    document_text: str


@dataclass(frozen=True)
class ProvenanceCheck:
    edge: EdgeTriple
    document_id: str
    evidence: str
    valid: bool
    reason: str  # "" when valid, else why it failed


@dataclass
class ProvenanceReport:
    total: int
    valid: int
    rate: float
    invalid: list[ProvenanceCheck] = field(default_factory=list)


def _norm_ws(text: str) -> str:
    """Whitespace-collapse + casefold, so a verbatim span matches its source regardless of casing
    and incidental whitespace differences."""
    return " ".join((text or "").split()).casefold()


def _phrase_norm(text: str) -> str:
    """Casefold + reduce every run of non-word characters to a single space, so an endpoint surface
    form is found regardless of glued punctuation (``"Stefan Vogel."`` -> ``"stefan vogel"``).
    Unicode-aware (``\\w`` keeps umlauts/ß), so German names survive."""
    return re.sub(r"[^\w]+", " ", text.casefold(), flags=re.UNICODE).strip()


def _contains_phrase(haystack: str, needle: str) -> bool:
    """Token-boundary phrase containment (both already ``_phrase_norm``-ed)."""
    if not needle:
        return False
    return f" {needle} " in f" {haystack} "


def check_provenance(item: ProvenanceInput) -> ProvenanceCheck:
    """Validate one edge's provenance: non-empty evidence, evidence is a (normalized) substring of
    the cited document, and both endpoint surface forms appear within the evidence span."""
    evidence_norm = _norm_ws(item.evidence)
    doc_norm = _norm_ws(item.document_text)

    def fail(reason: str) -> ProvenanceCheck:
        return ProvenanceCheck(
            edge=item.edge,
            document_id=item.document_id,
            evidence=item.evidence,
            valid=False,
            reason=reason,
        )

    if not evidence_norm:
        return fail("empty evidence")
    if evidence_norm not in doc_norm:
        return fail("evidence not found in source document")
    # Endpoint presence is checked on a punctuation-stripped view so glued punctuation
    # ("Stefan Vogel." / "Deutsche Bank,") does not look like a missing endpoint.
    span = _phrase_norm(item.evidence)
    if not _contains_phrase(span, _phrase_norm(item.edge.subject)):
        return fail(f"subject '{item.edge.subject}' not in evidence span")
    if not _contains_phrase(span, _phrase_norm(item.edge.object)):
        return fail(f"object '{item.edge.object}' not in evidence span")
    return ProvenanceCheck(
        edge=item.edge,
        document_id=item.document_id,
        evidence=item.evidence,
        valid=True,
        reason="",
    )


def evaluate_provenance(items: Sequence[ProvenanceInput]) -> ProvenanceReport:
    """Fraction of edge-provenance rows that are valid, plus the offending rows."""
    checks = [check_provenance(item) for item in items]
    valid = sum(1 for c in checks if c.valid)
    total = len(checks)
    return ProvenanceReport(
        total=total,
        valid=valid,
        rate=round(valid / total, 4) if total else 0.0,
        invalid=[c for c in checks if not c.valid],
    )
