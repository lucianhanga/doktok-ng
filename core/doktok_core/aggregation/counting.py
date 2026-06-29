"""Deterministic document counting for chat - "how many m-net invoices?" (ADR-0022).

RAG only sees a top-k retrieval window, so it under-counts (the bug that answered "8"); and the
record-aggregation path counts ``card_transaction`` line-items, never documents. This routes a
count/enumeration question to an exact SQL ``COUNT`` over the document corpus instead, reporting the
count under each lens it can compute - documents whose title/name carries the term, and documents
that *mention* it - and never conflating documents with transactions.

Mirrors the aggregation router's shape: a cheap regex gate, deterministic work, and ``None`` on
anything it can't confidently handle so chat falls back to semantic RAG. No LLM, no agent loop - the
count is always produced by the database, never phrased into existence by the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from doktok_contracts.ports import DocumentRepository, EntityRepository
from doktok_contracts.schemas import Citation, DocumentStatus, RagAnswer

# Cheap gate: a counting verb AND a document-type noun. The doc noun is what separates a document
# count ("how many invoices") from a record aggregation ("how much did I spend") - the latter has no
# doc noun and falls through to the aggregation router. "records" is deliberately excluded: it reads
# as transactions, not documents.
_COUNT_VERB = re.compile(
    r"\b(how many|number of|count of|count the|how much|list all|list every|enumerate)\b",
    re.IGNORECASE,
)
_DOC_NOUN = re.compile(
    r"\b(documents?|files?|invoices?|bills?|letters?|statements?|contracts?|receipts?|"
    r"reports?|emails?|scans?|pdfs?|pages?)\b",
    re.IGNORECASE,
)
# Filler stripped when isolating the entity surface from the question (everything that is not the
# entity: the count verb and doc noun are removed separately).
_FILLER = re.compile(
    r"\b(in|the|system|are|there|is|was|were|do|does|did|i|ive|i've|have|has|of|from|for|by|with|"
    r"my|our|all|any|please|me|we|us|on|at|to|stored|saved|kept|currently|total|so|far|about)\b",
    re.IGNORECASE,
)
_SAMPLE_CITATIONS = 6
# Generic doc nouns that mean "any document" - no document-type caveat is warranted for these.
_GENERIC_DOC_TYPES = frozenset({"document", "file", "pdf", "scan", "page"})


@dataclass(frozen=True)
class CountIntent:
    """A parsed count question: the entity surface to count for (None = whole corpus) and the
    document-type noun the user used ("invoice"), if any."""

    entity: str | None
    doc_type: str | None


@dataclass(frozen=True)
class CountLens:
    """One way of counting documents for the question, with its exact total and a few sample ids."""

    label: str
    count: int
    truncated: bool
    sample_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CountReport:
    entity: str | None
    doc_type: str | None
    lenses: list[CountLens]
    note: str | None = None


def looks_like_count(question: str) -> bool:
    """True if the question is a document count/enumeration - the gate before any work."""
    return bool(_COUNT_VERB.search(question) and _DOC_NOUN.search(question))


def _singular(noun: str) -> str:
    return noun[:-1] if len(noun) > 3 and noun.endswith("s") else noun


def parse_count_intent(question: str) -> CountIntent | None:
    """Extract a typed count intent, or None if the question is not a document count (-> RAG)."""
    if not looks_like_count(question):
        return None
    doc = _DOC_NOUN.search(question)
    doc_type = _singular(doc.group(0).lower()) if doc else None
    stripped = _COUNT_VERB.sub(" ", question)
    stripped = _DOC_NOUN.sub(" ", stripped)
    stripped = _FILLER.sub(" ", stripped)
    # Keep entity-ish chars (letters, digits, hyphen, dot, @, ampersand); drop other punctuation.
    stripped = re.sub(r"[^\w\s.\-@&]", " ", stripped)
    entity = " ".join(stripped.split()).strip() or None
    return CountIntent(entity=entity, doc_type=doc_type)


def count_documents(
    tenant_id: str,
    intent: CountIntent,
    *,
    documents: DocumentRepository,
    entities: EntityRepository,
) -> CountReport:
    """Compute the document count under each lens we can answer exactly (title, mention)."""
    lenses: list[CountLens] = []
    entity = intent.entity
    if entity:
        title_ids, title_total, title_tr = documents.list_document_ids(
            tenant_id, title=entity, status=DocumentStatus.ACTIVE
        )
        lenses.append(CountLens("with it in the title or name", title_total, title_tr, title_ids))
        men_ids, men_total, men_tr = entities.mention_document_ids(tenant_id, entity)
        lenses.append(CountLens("that mention it", men_total, men_tr, men_ids))
    else:
        ids, total, tr = documents.list_document_ids(tenant_id, status=DocumentStatus.ACTIVE)
        lenses.append(CountLens("in the system", total, tr, ids))
    note = None
    if intent.doc_type and intent.doc_type not in _GENERIC_DOC_TYPES:
        note = (
            f"Filtering to only {intent.doc_type}s is not available yet, "
            f"so this counts all matching documents."
        )
    return CountReport(entity=entity, doc_type=intent.doc_type, lenses=lenses, note=note)


def count_answer(
    report: CountReport,
    documents: DocumentRepository,
    tenant_id: str,
) -> RagAnswer | None:
    """Format a CountReport as a grounded RagAnswer, or None when nothing matched (-> RAG)."""
    if not any(lens.count > 0 for lens in report.lenses):
        return None
    clauses = [
        f"{'at least ' if lens.truncated else ''}{lens.count} {lens.label}"
        for lens in report.lenses
    ]
    if report.entity:
        joined = clauses[0] if len(clauses) == 1 else "; ".join(clauses)
        body = f"For “{report.entity}” I found {joined}."
    else:
        body = f"There are {clauses[0]}."
    if report.note:
        body = f"{body} {report.note}"
    return RagAnswer(
        answer=body,
        citations=_citations(report, documents, tenant_id),
        grounded=True,
    )


def _citations(
    report: CountReport, documents: DocumentRepository, tenant_id: str
) -> list[Citation]:
    seen: set[str] = set()
    out: list[Citation] = []
    for lens in report.lenses:
        for doc_id in lens.sample_ids:
            if doc_id in seen or len(out) >= _SAMPLE_CITATIONS:
                continue
            seen.add(doc_id)
            doc = documents.get(tenant_id, doc_id)
            if doc is None:
                continue
            snippet = (doc.title or doc.summary or doc.original_filename or "(document)").strip()[
                :160
            ]
            out.append(
                Citation(
                    index=len(out) + 1,
                    document_id=doc_id,
                    chunk_id=f"document:{doc_id}",
                    original_filename=doc.original_filename,
                    title=doc.title,
                    snippet=snippet or "(document)",
                    source_kind="document",
                )
            )
    return out
