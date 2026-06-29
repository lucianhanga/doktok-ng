"""Chat endpoint (brief section 18). Semantic RAG by default, with a deterministic shortcut for
aggregation questions ("how much did I spend at X") answered from structured records (M6.3 #158).

Conversations can be persisted server-side (M6.4 #248): pass a ``thread_id`` and the history is
loaded from the DB and the turn is saved; without one, chat stays stateless (client-held history).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Annotated

from doktok_contracts.ports import ChatThreadRepository, RagAnswerer, ToolCallingChatModel
from doktok_contracts.schemas import (
    ChatEvent,
    ChatMessage,
    ChatRequest,
    ChatThread,
    ChatThreadUpdate,
    ChatTurn,
    Citation,
    RagAnswer,
    RankedChunk,
    TurnMetrics,
)
from doktok_core.agent import run_agent, run_agent_stream
from doktok_core.aggregation import (
    aggregation_answer,
    count_answer,
    count_documents,
    looks_like_aggregation,
    parse_count_intent,
    route_to_intent,
)
from doktok_core.tools import ToolGateway
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from doktok_api.dependencies import (
    Tenant,
    get_chat_model,
    get_chat_thread_repository,
    get_document_repository,
    get_entity_repository,
    get_rag_answerer,
    get_record_repository,
    get_tool_registry,
)

logger = logging.getLogger("doktok.api.chat")

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

Answerer = Annotated[RagAnswerer, Depends(get_rag_answerer)]
Threads = Annotated[ChatThreadRepository, Depends(get_chat_thread_repository)]


def _try_count(question: str, http_request: Request, tenant_id: str) -> RagAnswer | None:
    """Deterministic document-count answer ("how many m-net invoices") via exact SQL COUNT, or None
    to fall back. Tried before aggregation so a document count is never miscounted as transactions
    (ADR-0022). Best-effort: any failure returns None so chat always degrades to RAG."""
    intent = parse_count_intent(question)
    if intent is None:
        return None
    try:
        documents = get_document_repository(http_request)
        report = count_documents(
            tenant_id, intent, documents=documents, entities=get_entity_repository(http_request)
        )
        return count_answer(report, documents, tenant_id)  # None when nothing matched -> RAG
    except Exception:  # noqa: BLE001 - routing is additive; never break chat
        logger.debug("count routing failed; falling back to RAG", exc_info=True)
        return None


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


def _try_structured(question: str, http_request: Request, tenant_id: str) -> RagAnswer | None:
    """The deterministic shortcuts, in precedence order: a document count first (most specific),
    then record aggregation. None means neither matched - the caller falls back to semantic RAG."""
    return _try_count(question, http_request, tenant_id) or _try_aggregation(
        question, http_request, tenant_id
    )


def _agent_model(http_request: Request) -> ToolCallingChatModel | None:
    """The configured chat model if it supports tool-calling, else None (-> classic fallback)."""
    model = get_chat_model(http_request)
    return model if isinstance(model, ToolCallingChatModel) else None


def _load_thread(threads: Threads, tenant_id: str, thread_id: str, question: str) -> list[ChatTurn]:
    """Validate the thread, load its history as turns, and persist the new user message.

    Raises 404 if the thread does not belong to the tenant. The returned history is the prior turns
    (the just-appended user message is not included - that is this turn's ``question``).
    """
    if not threads.thread_exists(tenant_id, thread_id):
        raise HTTPException(status_code=404, detail="thread not found")
    history = [
        ChatTurn(role=m.role, content=m.content) for m in threads.get_messages(tenant_id, thread_id)
    ]
    threads.append_message(tenant_id, thread_id, "user", question)
    return history


# ---- Thread management (M6.4 #248) ----


@router.post("/threads", response_model=ChatThread)
def create_thread(tenant: Tenant, threads: Threads) -> ChatThread:
    return threads.create_thread(tenant.tenant_id)


@router.get("/threads", response_model=list[ChatThread])
def list_threads(tenant: Tenant, threads: Threads) -> list[ChatThread]:
    return threads.list_threads(tenant.tenant_id)


@router.get("/threads/{thread_id}/messages", response_model=list[ChatMessage])
def thread_messages(thread_id: str, tenant: Tenant, threads: Threads) -> list[ChatMessage]:
    if not threads.thread_exists(tenant.tenant_id, thread_id):
        raise HTTPException(status_code=404, detail="thread not found")
    return threads.get_messages(tenant.tenant_id, thread_id)


@router.patch("/threads/{thread_id}", response_model=ChatThread)
def rename_thread(
    thread_id: str, body: ChatThreadUpdate, tenant: Tenant, threads: Threads
) -> ChatThread:
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title must not be blank")
    updated = threads.update_title(tenant.tenant_id, thread_id, title)
    if updated is None:
        raise HTTPException(status_code=404, detail="thread not found")
    return updated


@router.delete("/threads/{thread_id}", status_code=204)
def delete_thread(thread_id: str, tenant: Tenant, threads: Threads) -> None:
    threads.delete_thread(tenant.tenant_id, thread_id)


@router.delete("/threads/{thread_id}/messages/{message_id}/after", status_code=204)
def truncate_thread(thread_id: str, message_id: str, tenant: Tenant, threads: Threads) -> None:
    """Delete a message and everything after it (truncate the conversation) - used when a question
    is deleted or edited. Idempotent: a missing message simply removes nothing."""
    threads.delete_messages_from(tenant.tenant_id, thread_id, message_id)


# ---- Chat ----


@router.post("", response_model=RagAnswer)
def chat(
    request: ChatRequest,
    http_request: Request,
    tenant: Tenant,
    answerer: Answerer,
    threads: Threads,
) -> RagAnswer:
    question = request.question
    limit = max(1, min(request.limit, 20))
    tenant_id = tenant.tenant_id
    history = request.history
    if request.thread_id:
        history = _load_thread(threads, tenant_id, request.thread_id, question)

    agent = _agent_model(http_request) if request.agent_mode == "agent" else None
    if agent is not None:
        registry = get_tool_registry(http_request)
        answer = run_agent(
            tenant_id,
            question,
            model=agent,
            gateway=ToolGateway(registry),
            tool_specs=registry.specs(),
            history=history,
        )
    else:
        structured = _try_structured(question, http_request, tenant_id)
        answer = (
            structured
            if structured is not None
            else answerer.answer_thread(tenant_id, history, question, limit)
        )
    if request.thread_id:
        threads.append_message(
            tenant_id,
            request.thread_id,
            "assistant",
            answer.answer,
            citations=answer.citations,
            ranking=answer.ranking,
            metrics=answer.metrics,
        )
    return answer


def _sse(event: ChatEvent) -> str:
    return f"event: {event.type}\ndata: {json.dumps(event.model_dump())}\n\n"


@router.post("/stream")
def chat_stream(
    request: ChatRequest,
    http_request: Request,
    tenant: Tenant,
    answerer: Answerer,
    threads: Threads,
) -> StreamingResponse:
    """Streaming chat (M6.4, ADR-0018 Phase 3): SSE of meta/reasoning/token/sources/done. Mirrors
    the JSON endpoint, including the aggregation shortcut and optional thread persistence."""
    question = request.question
    limit = max(1, min(request.limit, 20))
    tenant_id = tenant.tenant_id
    # Validate + persist the user turn synchronously so a bad thread_id 404s before streaming.
    history = request.history
    if request.thread_id:
        history = _load_thread(threads, tenant_id, request.thread_id, question)

    def events() -> Iterator[str]:
        parts: list[str] = []
        reason_parts: list[str] = []
        citations: list[Citation] = []
        ranking: list[RankedChunk] = []
        metrics: TurnMetrics | None = None
        agent = _agent_model(http_request) if request.agent_mode == "agent" else None
        structured = (
            None if agent is not None else _try_structured(question, http_request, tenant_id)
        )
        if agent is not None:
            registry = get_tool_registry(http_request)
            yield _sse(ChatEvent(type="meta"))
            for event in run_agent_stream(
                tenant_id,
                question,
                model=agent,
                gateway=ToolGateway(registry),
                tool_specs=registry.specs(),
                history=history,
            ):
                if event.type == "token":
                    parts.append(event.delta)
                elif event.type == "sources":
                    citations = event.citations
                yield _sse(event)
        elif structured is not None:
            yield _sse(ChatEvent(type="meta"))
            yield _sse(ChatEvent(type="token", delta=structured.answer))
            yield _sse(ChatEvent(type="sources", citations=structured.citations))
            yield _sse(ChatEvent(type="done", grounded=structured.grounded))
            parts.append(structured.answer)
            citations = structured.citations
        else:
            for event in answerer.answer_thread_stream(
                tenant_id, history, question, limit, reasoning=request.reasoning
            ):
                if event.type == "token":
                    parts.append(event.delta)
                elif event.type == "reasoning":
                    reason_parts.append(event.delta)
                elif event.type == "sources":
                    citations = event.citations
                elif event.type == "ranking":
                    ranking = event.ranking
                elif event.type == "metrics":
                    metrics = event.metrics
                yield _sse(event)
        answer_text = "".join(parts).strip()
        if request.thread_id and answer_text:
            threads.append_message(
                tenant_id,
                request.thread_id,
                "assistant",
                answer_text,
                reasoning="".join(reason_parts).strip(),
                citations=citations,
                ranking=ranking,
                metrics=metrics,
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
