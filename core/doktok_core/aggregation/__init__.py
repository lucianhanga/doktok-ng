"""Structured aggregation (M6.3): extract typed records for deterministic SUM/COUNT queries."""

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
    "InMemoryRecordRepository",
    "aggregation_answer",
    "looks_like_aggregation",
    "normalize_merchant",
    "normalize_transaction",
    "parse_amount_minor",
    "route_to_intent",
]
