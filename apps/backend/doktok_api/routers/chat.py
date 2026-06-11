"""Chat (RAG) endpoint (brief section 18). Tenant-scoped, token-protected."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import RagAnswerer
from doktok_contracts.schemas import ChatRequest, RagAnswer
from fastapi import APIRouter, Depends

from doktok_api.dependencies import Tenant, get_rag_answerer

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

Answerer = Annotated[RagAnswerer, Depends(get_rag_answerer)]


@router.post("", response_model=RagAnswer)
def chat(request: ChatRequest, tenant: Tenant, answerer: Answerer) -> RagAnswer:
    limit = max(1, min(request.limit, 20))
    return answerer.answer(tenant.tenant_id, request.question, limit)
