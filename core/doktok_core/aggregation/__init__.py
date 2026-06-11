"""Structured aggregation (M6.3): extract typed records for deterministic SUM/COUNT queries."""

from doktok_core.aggregation.inmemory import InMemoryRecordRepository
from doktok_core.aggregation.records import (
    normalize_merchant,
    normalize_transaction,
    parse_amount_minor,
)

__all__ = [
    "InMemoryRecordRepository",
    "normalize_merchant",
    "normalize_transaction",
    "parse_amount_minor",
]
