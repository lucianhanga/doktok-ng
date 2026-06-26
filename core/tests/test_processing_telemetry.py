"""Unit tests for the per-document processing telemetry builder (pure; no DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from doktok_contracts.schemas import (
    Document,
    DocumentFeature,
    DocumentStatus,
    FeatureMetrics,
    FeatureStatus,
)
from doktok_core.features.telemetry import (
    build_processing_summary,
    build_processing_telemetry,
)

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _doc(
    metadata: dict[str, object], *, status: DocumentStatus = DocumentStatus.ACTIVE
) -> Document:
    return Document(
        id="d1",
        tenant_id="t1",
        sha256="a" * 64,
        original_filename="f.pdf",
        status=status,
        created_at=BASE,
        ingested_at=BASE,
        activated_at=BASE + timedelta(seconds=5),
        metadata=metadata,
    )


def _feature(
    feature: str,
    *,
    status: FeatureStatus = FeatureStatus.DONE,
    metrics: FeatureMetrics | None = None,
    attempts: int = 1,
    last_error: str | None = None,
    created_at: datetime = BASE,
) -> DocumentFeature:
    return DocumentFeature(
        id=f"f-{feature}",
        tenant_id="t1",
        document_id="d1",
        feature=feature,
        status=status,
        attempts=attempts,
        last_error=last_error,
        last_attempt_at=created_at,
        completed_at=created_at + timedelta(seconds=1) if status is FeatureStatus.DONE else None,
        created_at=created_at,
        updated_at=created_at,
        metrics=metrics or FeatureMetrics(),
    )


def test_telemetry_ocr_outcome_done_when_method_is_ocr() -> None:
    doc = _doc({"extraction_method": "ocr", "page_count": 3, "ocr_confidence": 0.91})
    tel = build_processing_telemetry(doc, [])
    assert tel.ocr_outcome == "done"
    assert tel.page_count == 3
    assert tel.ocr_confidence == 0.91
    assert tel.activated_at == BASE + timedelta(seconds=5)


def test_telemetry_ocr_outcome_not_needed_for_born_digital() -> None:
    tel = build_processing_telemetry(_doc({"extraction_method": "pdf_text"}), [])
    assert tel.ocr_outcome == "not_needed"


def test_telemetry_ocr_outcome_failed_when_extract_feature_failed() -> None:
    doc = _doc({"extraction_method": ""})
    feats = [_feature("extract", status=FeatureStatus.FAILED, last_error="boom")]
    tel = build_processing_telemetry(doc, feats)
    assert tel.ocr_outcome == "failed"


def test_telemetry_steps_durations_tokens_and_totals() -> None:
    doc = _doc({"extraction_method": "pdf_text", "normalized_from": "application/x-docx"})
    feats = [
        _feature(
            "doc_metadata",
            metrics=FeatureMetrics(
                duration_ms=1200, prompt_tokens=400, answer_tokens=100, model="qwen3:14b"
            ),
            created_at=BASE,
        ),
        _feature(
            "chunk_embed",
            metrics=FeatureMetrics(duration_ms=800, prompt_tokens=50),
            created_at=BASE + timedelta(seconds=2),
        ),
        # A measured-but-tokenless step (e.g. thumbnail): duration counts, tokens stay None.
        _feature(
            "thumbnail",
            metrics=FeatureMetrics(duration_ms=300),
            created_at=BASE + timedelta(seconds=4),
        ),
    ]
    tel = build_processing_telemetry(doc, feats)

    assert tel.normalized_from_mime == "application/x-docx"
    by_feature = {s.feature: s for s in tel.steps}
    meta = by_feature["doc_metadata"]
    assert meta.duration_ms == 1200
    assert meta.prompt_tokens == 400
    assert meta.answer_tokens == 100
    assert meta.total_tokens == 500  # validator filled from parts
    assert meta.model == "qwen3:14b"

    thumb = by_feature["thumbnail"]
    assert thumb.duration_ms == 300
    assert thumb.total_tokens is None  # no LLM tokens -> None, not 0

    # Server-side sums over measured values only.
    assert tel.total_duration_ms == 1200 + 800 + 300
    assert tel.total_tokens == 500 + 50


def test_telemetry_derives_duration_from_timestamps_when_metrics_empty() -> None:
    # Documents processed before the metrics column existed have empty metrics but real
    # last_attempt_at -> completed_at timestamps; derive a coarse (estimated) duration from them.
    # Tokens cannot be recovered, so they stay None.
    doc = _doc({"extraction_method": "text"})
    tel = build_processing_telemetry(doc, [_feature("doc_metadata")])  # 1s apart in the helper
    step = tel.steps[0]
    assert step.duration_ms == 1000
    assert step.estimated is True
    assert step.total_tokens is None
    assert tel.total_duration_ms == 1000
    assert tel.total_tokens == 0


def test_telemetry_no_duration_when_timestamps_missing() -> None:
    # A step that never completed (pending) has no completion timestamp -> no derivable duration.
    doc = _doc({"extraction_method": "text"})
    tel = build_processing_telemetry(doc, [_feature("doc_metadata", status=FeatureStatus.PENDING)])
    step = tel.steps[0]
    assert step.duration_ms is None
    assert step.estimated is False
    assert tel.total_duration_ms == 0


def test_summary_metadata_fields_and_counts() -> None:
    doc = _doc(
        {"extraction_method": "ocr", "page_count": 2, "normalized_from": "application/x-xlsx"}
    )
    summary = build_processing_summary(doc, features_done=4, features_failed=1)
    assert summary.extraction_method == "ocr"
    assert summary.ocr_outcome == "done"
    assert summary.page_count == 2
    assert summary.normalized_from_mime == "application/x-xlsx"
    assert summary.status == "active"
    assert summary.features_done == 4
    assert summary.features_failed == 1
