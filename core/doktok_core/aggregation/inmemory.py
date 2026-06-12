"""In-memory record repository for tests/dev (tenant-scoped). M6.3."""

from __future__ import annotations

from datetime import date

from doktok_contracts.schemas import (
    AggregationBucket,
    AggregationIntent,
    AggregationResult,
    ExtractedRecord,
)

_MIN_DATE = date.min


class InMemoryRecordRepository:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], list[ExtractedRecord]] = {}

    def replace_for_document(
        self, tenant_id: str, document_id: str, records: list[ExtractedRecord]
    ) -> None:
        self._records[(tenant_id, document_id)] = [r.model_copy(deep=True) for r in records]

    def list_for_document(self, tenant_id: str, document_id: str) -> list[ExtractedRecord]:
        return [r.model_copy(deep=True) for r in self._records.get((tenant_id, document_id), [])]

    def aggregate(self, tenant_id: str, intent: AggregationIntent) -> AggregationResult:
        # Space-insensitive substring so "block house" matches "BLOCKHOUSE #42" (mirrors the SQL).
        merchant = intent.merchant.strip().lower().replace(" ", "") if intent.merchant else None

        def merchant_ok(r: ExtractedRecord) -> bool:
            if merchant is None:
                return True
            norm = r.merchant_normalized
            return norm is not None and merchant in norm.replace(" ", "")

        matched = [
            r
            for (tid, _), recs in self._records.items()
            if tid == tenant_id
            for r in recs
            if (intent.record_type is None or r.record_type == intent.record_type)
            and (intent.direction is None or r.direction == intent.direction)
            and (intent.currency is None or r.currency == intent.currency)
            and (intent.date_from is None or (r.occurred_on and r.occurred_on >= intent.date_from))
            and (intent.date_to is None or (r.occurred_on and r.occurred_on <= intent.date_to))
            and merchant_ok(r)
        ]
        totals: dict[str | None, list[int]] = {}
        for r in matched:
            bucket = totals.setdefault(r.currency, [0, 0])
            bucket[0] += r.amount_minor or 0
            bucket[1] += 1
        buckets = [
            AggregationBucket(currency=cur, total_minor=tot, count=cnt)
            for cur, (tot, cnt) in sorted(totals.items(), key=lambda kv: -kv[1][1])
        ]
        samples = sorted(matched, key=lambda r: r.occurred_on or _MIN_DATE, reverse=True)[
            : intent.sample_limit
        ]
        return AggregationResult(
            operation=intent.operation,
            count=len(matched),
            by_currency=buckets,
            samples=[r.model_copy(deep=True) for r in samples],
        )
