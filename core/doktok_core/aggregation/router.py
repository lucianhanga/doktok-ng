"""Route a chat question to deterministic record aggregation (M6.3 #158).

A cheap keyword gate plus an LLM slot-fill turn questions like "how much did I spend at Block House"
into a typed ``AggregationIntent`` answered by parameterized SUM/COUNT over ``extracted_records`` -
the beyond-RAG path. Anything else, or any parse failure, returns None so the caller falls back to
semantic RAG: the router only ever *adds* a deterministic shortcut, it never breaks chat.
"""

from __future__ import annotations

import json
import re
from datetime import date

from doktok_contracts.ports import ChatModelProvider, DocumentRepository
from doktok_contracts.schemas import (
    AggregationIntent,
    AggregationResult,
    Citation,
    ExtractedRecord,
    RagAnswer,
)

# Cheap gate so ordinary semantic questions skip the LLM slot-fill entirely (latency + cost).
_AGG_HINTS = re.compile(
    r"\b(how much|how many|total|totals|sum|spent|spend|spending|paid|count)\b", re.IGNORECASE
)

_SLOT_PROMPT = """You convert a question about the user's financial documents into a JSON filter
for a transaction database. Reply with ONLY a JSON object and no prose:
{{"is_aggregation": true|false, "operation": "sum"|"count", "merchant": string|null,
"direction": "debit"|"credit"|null, "currency": string|null,
"date_from": "YYYY-MM-DD"|null, "date_to": "YYYY-MM-DD"|null}}
Rules: is_aggregation is true only if the user wants a total amount or a count of transactions
(spending at a merchant, total paid, number of payments). operation is "count" for "how many",
otherwise "sum". merchant is the shop/payee name if one is named, else null. direction is "debit"
for spending and "credit" for income/refunds, else null. Use null whenever unsure.
Question: {question}
JSON:"""


def looks_like_aggregation(question: str) -> bool:
    """True if the question even might be a total/count - the gate before the LLM slot-fill."""
    return bool(_AGG_HINTS.search(question))


def route_to_intent(question: str, chat_model: ChatModelProvider) -> AggregationIntent | None:
    """Return a typed intent if the question is a record aggregation, else None (-> RAG)."""
    if not looks_like_aggregation(question):
        return None
    try:
        data = _first_json_object(chat_model.complete(_SLOT_PROMPT.format(question=question)))
    except Exception:  # noqa: BLE001 - routing must never break chat; fall back to RAG
        return None
    if not isinstance(data, dict) or not data.get("is_aggregation"):
        return None
    direction = data.get("direction")
    return AggregationIntent(
        operation="count" if data.get("operation") == "count" else "sum",
        merchant=_clean_str(data.get("merchant")),
        direction=direction if direction in ("debit", "credit") else None,
        currency=_clean_str(data.get("currency")),
        date_from=_parse_date(data.get("date_from")),
        date_to=_parse_date(data.get("date_to")),
    )


def aggregation_answer(
    intent: AggregationIntent,
    result: AggregationResult,
    documents: DocumentRepository,
    tenant_id: str,
) -> RagAnswer:
    """Format a typed aggregation as a grounded RagAnswer: the total/count + per-currency rollup,
    with the contributing documents as citations for provenance."""
    if intent.operation == "count":
        body = f"{result.count} matching record(s)."
    elif result.by_currency:
        body = "; ".join(
            f"{_money(b.total_minor)} {b.currency or ''}".strip() + f" ({b.count} record(s))"
            for b in result.by_currency
        )
    else:
        body = "no matching records."
    answer = f"{_describe(intent)}: {body}"
    return RagAnswer(
        answer=answer,
        citations=_citations(result.samples, documents, tenant_id),
        grounded=result.count > 0,
    )


def _citations(
    samples: list[ExtractedRecord], documents: DocumentRepository, tenant_id: str
) -> list[Citation]:
    out: list[Citation] = []
    seen: set[str] = set()
    for rec in samples:
        if rec.document_id in seen:
            continue
        seen.add(rec.document_id)
        doc = documents.get(tenant_id, rec.document_id)
        snippet = (rec.raw_text or rec.description or "").strip()[:160] or "(record)"
        out.append(
            Citation(
                index=len(out) + 1,
                document_id=rec.document_id,
                chunk_id=f"record:{rec.id}",
                original_filename=doc.original_filename if doc else None,
                title=doc.title if doc else None,
                snippet=snippet,
                source_kind="transaction",
            )
        )
    return out


def _describe(intent: AggregationIntent) -> str:
    verb = "Count of" if intent.operation == "count" else "Total"
    parts = [verb, "records" if intent.operation == "count" else "spend"]
    if intent.direction:
        parts.append(f"({intent.direction})")
    if intent.merchant:
        parts.append(f"at '{intent.merchant}'")
    if intent.currency:
        parts.append(f"in {intent.currency}")
    if intent.date_from or intent.date_to:
        parts.append(f"from {intent.date_from or '…'} to {intent.date_to or '…'}")
    return " ".join(parts)


def _money(total_minor: int) -> str:
    return f"{total_minor / 100:.2f}"


def _clean_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _parse_date(value: object) -> date | None:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _first_json_object(text: str) -> object:
    """Parse the first balanced ``{...}`` object from an LLM reply (tolerates surrounding prose)."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None
