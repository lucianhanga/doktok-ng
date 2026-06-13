"""Chat endpoint (brief section 18). Semantic RAG by default, with a deterministic shortcut for
aggregation questions ("how much did I spend at X") answered from structured records (M6.3 #158)."""

from __future__ import annotations

import logging
from typing import Annotated

from doktok_contracts.ports import RagAnswerer
from doktok_contracts.schemas import ChatRequest, RagAnswer
from doktok_core.aggregation import aggregation_answer, looks_like_aggregation, route_to_intent
from fastapi import APIRouter, Depends, Request

from doktok_api.dependencies import (
    Tenant,
    get_chat_model,
    get_document_repository,
    get_rag_answerer,
    get_record_repository,
)

logger = logging.getLogger("doktok.api.chat")

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

Answerer = Annotated[RagAnswerer, Depends(get_rag_answerer)]


def _try_aggregation(question: str, http_request: Request, tenant_id: str) -> RagAnswer | None:
    """Deterministic beyond-RAG answer for a total/count question, or None to fall back to RAG.

    Best-effort: any failure (model, records, or even dependency resolution) returns None so the
    chat endpoint always degrades to semantic RAG rather than erroring.
    """
    if not looks_like_aggregation(question):
        return None
    try:
        intent = route_to_intent(question, get_chat_model(http_request))
        if intent is None:
            return None
        result = get_record_repository(http_request).aggregate(tenant_id, intent)
        if result.count == 0:  # not actually an aggregation hit; let RAG try
            return None
        return aggregation_answer(intent, result, get_document_repository(http_request), tenant_id)
    except Exception:  # noqa: BLE001 - routing is additive; never break chat
        logger.debug("aggregation routing failed; falling back to RAG", exc_info=True)
        return None


@router.post("", response_model=RagAnswer)
def chat(
    request: ChatRequest, http_request: Request, tenant: Tenant, answerer: Answerer
) -> RagAnswer:
    question = request.question
    limit = max(1, min(request.limit, 20))
    structured = _try_aggregation(question, http_request, tenant.tenant_id)
    if structured is not None:
        return structured
    # Multi-turn (ADR-0018): rewrite the follow-up against the conversation, then answer grounded.
    # Empty history degrades to single-turn answering.
    return answerer.answer_thread(tenant.tenant_id, request.history, question, limit)
