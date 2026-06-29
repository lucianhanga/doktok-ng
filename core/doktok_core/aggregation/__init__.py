"""Structured aggregation (M6.3): extract typed records for deterministic SUM/COUNT queries."""

from doktok_core.aggregation.counting import (
    CountIntent,
    CountReport,
    count_answer,
    count_documents,
    looks_like_count,
    parse_count_intent,
)
from doktok_core.aggregation.inmemory import InMemoryRecordRepository
from doktok_core.aggregation.records import (
    normalize_merchant,
    normalize_transaction,
    parse_amount_minor,
)
from doktok_core.aggregation.router import (
    aggregation_answer,
    looks_like_aggregation,
    route_to_intent,
)

__all__ = [
    "CountIntent",
    "CountReport",
    "InMemoryRecordRepository",
    "aggregation_answer",
    "count_answer",
    "count_documents",
    "looks_like_aggregation",
    "looks_like_count",
    "normalize_merchant",
    "normalize_transaction",
    "parse_amount_minor",
    "parse_count_intent",
    "route_to_intent",
]
