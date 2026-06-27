"""In-memory record repository for tests/dev (tenant-scoped). M6.3."""

from __future__ import annotations

from datetime import date

from doktok_contracts.schemas import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    AggregationBucket,
    AggregationIntent,
    AggregationResult,
    ConfidenceBuckets,
    DocumentRecordSummary,
    ExtractedRecord,
    MerchantRollup,
    RecordCurrencyRollup,
    RecordTypeCount,
)

_MIN_DATE = date.min
_TOP_MERCHANTS = 5


class InMemoryRecordRepository:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], list[ExtractedRecord]] = {}

    def replace_for_document(
        self, tenant_id: str, document_id: str, records: list[ExtractedRecord]
    ) -> None:
        self._records[(tenant_id, document_id)] = [r.model_copy(deep=True) for r in records]

    def list_for_document(self, tenant_id: str, document_id: str) -> list[ExtractedRecord]:
        return [r.model_copy(deep=True) for r in self._records.get((tenant_id, document_id), [])]

    def _ordered(self, tenant_id: str, document_id: str) -> list[ExtractedRecord]:
        # occurred_on ASC NULLS LAST, then id (mirrors the SQL ordering).
        return sorted(
            self._records.get((tenant_id, document_id), []),
            key=lambda r: (r.occurred_on is None, r.occurred_on or _MIN_DATE, r.id),
        )

    def list_for_document_page(
        self, tenant_id: str, document_id: str, *, limit: int, offset: int
    ) -> tuple[list[ExtractedRecord], int]:
        ordered = self._ordered(tenant_id, document_id)
        page = ordered[offset : offset + limit]
        return [r.model_copy(deep=True) for r in page], len(ordered)

    def record_summary(self, tenant_id: str, document_id: str) -> DocumentRecordSummary:
        recs = self._records.get((tenant_id, document_id), [])
        if not recs:
            return DocumentRecordSummary()

        # Per-currency debit/credit/count (money never summed across currencies).
        cur: dict[str | None, list[int]] = {}  # currency -> [debit, credit, count]
        for r in recs:
            row = cur.setdefault(r.currency, [0, 0, 0])
            if r.direction == "debit":
                row[0] += r.amount_minor or 0
            elif r.direction == "credit":
                row[1] += r.amount_minor or 0
            row[2] += 1
        by_currency = [
            RecordCurrencyRollup(currency=c, debit_minor=v[0], credit_minor=v[1], count=v[2])
            for c, v in sorted(cur.items(), key=lambda kv: (-kv[1][2], kv[0] or ""))
        ]

        # Record-type counts.
        types: dict[str, int] = {}
        for r in recs:
            types[r.record_type] = types.get(r.record_type, 0) + 1
        by_type = [
            RecordTypeCount(record_type=t, count=n)
            for t, n in sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

        # Top merchants by count, per (merchant, currency).
        merch: dict[tuple[str, str | None], list[int]] = {}  # -> [count, total_minor]
        for r in recs:
            if not r.merchant_normalized:
                continue
            row = merch.setdefault((r.merchant_normalized, r.currency), [0, 0])
            row[0] += 1
            row[1] += r.amount_minor or 0
        top_merchants = [
            MerchantRollup(merchant=m, currency=c, count=v[0], total_minor=v[1])
            for (m, c), v in sorted(merch.items(), key=lambda kv: (-kv[1][0], kv[0][0]))[
                :_TOP_MERCHANTS
            ]
        ]

        # Confidence buckets - only non-NULL rows are bucketed; NULL counts as unscored.
        buckets = ConfidenceBuckets()
        for r in recs:
            c = r.confidence
            if c is None:
                buckets.unscored += 1
            elif c >= CONFIDENCE_HIGH:
                buckets.high += 1
            elif c >= CONFIDENCE_MEDIUM:
                buckets.medium += 1
            else:
                buckets.low += 1

        dates = [r.occurred_on for r in recs if r.occurred_on is not None]
        return DocumentRecordSummary(
            total=len(recs),
            by_currency=by_currency,
            by_type=by_type,
            date_from=min(dates) if dates else None,
            date_to=max(dates) if dates else None,
            top_merchants=top_merchants,
            confidence=buckets,
            low_confidence_count=buckets.low,
        )

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
