"""Chat endpoint (brief section 18). Semantic RAG by default, with a deterministic shortcut for
aggregation questions ("how much did I spend at X") answered from structured records (M6.3 #158)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Annotated

from doktok_contracts.ports import RagAnswerer
from doktok_contracts.schemas import ChatEvent, ChatRequest, RagAnswer
from doktok_core.aggregation import aggregation_answer, looks_like_aggregation, route_to_intent
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

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


def _sse(event: ChatEvent) -> str:
    return f"event: {event.type}\ndata: {json.dumps(event.model_dump())}\n\n"


@router.post("/stream")
def chat_stream(
    request: ChatRequest, http_request: Request, tenant: Tenant, answerer: Answerer
) -> StreamingResponse:
    """Streaming chat (M6.4, ADR-0018 Phase 3): SSE of meta/reasoning/token/sources/done. Mirrors
    the JSON endpoint, including the aggregation shortcut (emitted as a one-shot stream)."""
    question = request.question
    limit = max(1, min(request.limit, 20))
    tenant_id = tenant.tenant_id

    def events() -> Iterator[str]:
        structured = _try_aggregation(question, http_request, tenant_id)
        if structured is not None:
            yield _sse(ChatEvent(type="meta"))
            yield _sse(ChatEvent(type="token", delta=structured.answer))
            yield _sse(ChatEvent(type="sources", citations=structured.citations))
            yield _sse(ChatEvent(type="done", grounded=structured.grounded))
            return
        for event in answerer.answer_thread_stream(
            tenant_id, request.history, question, limit, reasoning=request.reasoning
        ):
            yield _sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
