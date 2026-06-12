"""Deterministic aggregation over structured records (M6.3). Tenant-scoped, token-protected.

Answers enumeration/aggregation questions that top-k RAG cannot - e.g. "how much did I spend at
Block House across all statements" - by summing the typed `extracted_records` money spine. Takes a
typed intent (not text-to-SQL), so the query is always parameterized and tenant-scoped.
"""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import RecordRepository
from doktok_contracts.schemas import AggregationIntent, AggregationResult
from fastapi import APIRouter, Depends

from doktok_api.dependencies import Tenant, get_record_repository

router = APIRouter(prefix="/api/v1/aggregate", tags=["aggregate"])

Repo = Annotated[RecordRepository, Depends(get_record_repository)]


@router.post("", response_model=AggregationResult)
def aggregate(intent: AggregationIntent, tenant: Tenant, repo: Repo) -> AggregationResult:
    """Run a typed aggregation (sum/count) over the caller tenant's extracted records."""
    return repo.aggregate(tenant.tenant_id, intent)
