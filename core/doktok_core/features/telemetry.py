"""Build per-document processing telemetry from already-loaded data (no extra queries).

The detail view folds the document row's ``metadata`` (extraction outcome, page count, language,
office->PDF normalization) together with the feature ledger rows (per-step status, attempts,
duration and enrichment tokens from each row's ``metrics``) into a single ``ProcessingTelemetry``.
The list view derives a compact ``ProcessingSummary`` for the tooltip the same way, with the
done/failed counts supplied by the caller from one batched query (never per-row).

This lives in core (not the adapter) so the mapping is reused and unit-tested without a DB. It reads
the feature catalog for human labels and is pure: inputs in, telemetry out.
"""

from __future__ import annotations

from typing import Any

from doktok_contracts.schemas import (
    Document,
    DocumentFeature,
    FeatureStatus,
    ProcessingStep,
    ProcessingSummary,
    ProcessingTelemetry,
)

from doktok_core.features.catalog import FEATURE_CATALOG

# Human label per feature name (extends the catalog with the inline 'extract' marker, which has no
# reconciler processor and so is absent from the catalog).
_LABELS: dict[str, str] = {spec.name: spec.label for spec in FEATURE_CATALOG}
_LABELS.setdefault("extract", "Text extraction")

# Extraction methods that mean OCR actually ran (vs. born-digital text / not needed).
_OCR_METHODS = {"ocr", "pdf_mixed"}


def _label(feature: str) -> str:
    return _LABELS.get(feature, feature)


def _as_int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _ocr_outcome(extraction_method: str, *, has_failed_extract: bool) -> str:
    """Derive the OCR step outcome: 'done' when OCR ran, 'failed' when extraction/OCR failed,
    else 'not_needed' (born-digital text, plain text/markdown, etc.)."""
    if has_failed_extract:
        return "failed"
    return "done" if extraction_method in _OCR_METHODS else "not_needed"


def _step(feature: DocumentFeature) -> ProcessingStep:
    m = feature.metrics
    has_tokens = m.total_tokens > 0
    return ProcessingStep(
        feature=feature.feature,
        label=_label(feature.feature),
        status=feature.status.value,
        started_at=feature.last_attempt_at,
        completed_at=feature.completed_at,
        duration_ms=m.duration_ms if m.duration_ms > 0 else None,
        prompt_tokens=m.prompt_tokens if has_tokens else None,
        answer_tokens=m.answer_tokens if has_tokens else None,
        total_tokens=m.total_tokens if has_tokens else None,
        model=m.model or None,
        estimated=m.estimated,
        attempts=feature.attempts,
        last_error=feature.last_error,
    )


def build_processing_telemetry(
    document: Document, features: list[DocumentFeature]
) -> ProcessingTelemetry:
    """Fold the document metadata + feature ledger rows into the detail-view telemetry. Pure; uses
    only the passed-in data (no query). Backward compatible: empty metrics -> nulls/zeros."""
    meta: dict[str, Any] = document.metadata or {}
    extraction_method = str(meta.get("extraction_method", "") or "")
    failed_extract = any(
        f.feature == "extract" and f.status is FeatureStatus.FAILED for f in features
    )

    steps = [_step(f) for f in sorted(features, key=lambda f: (f.created_at, f.feature))]
    # Server-side sums over measured values only, so partial telemetry still totals correctly.
    total_duration_ms = sum(s.duration_ms for s in steps if s.duration_ms)
    total_tokens = sum(s.total_tokens for s in steps if s.total_tokens)

    ocr_conf = meta.get("ocr_confidence")
    page_count = meta.get("page_count")
    return ProcessingTelemetry(
        received_at=document.ingested_at or document.created_at,
        activated_at=document.activated_at,
        extraction_method=extraction_method,
        page_count=_as_int_or_none(page_count),
        ocr_outcome=_ocr_outcome(extraction_method, has_failed_extract=failed_extract),
        ocr_confidence=float(ocr_conf) if isinstance(ocr_conf, (int, float)) else None,
        normalized_from_mime=str(meta.get("normalized_from", "") or ""),
        language=str(meta.get("language", "") or ""),
        steps=steps,
        total_duration_ms=total_duration_ms,
        total_tokens=total_tokens,
    )


def build_processing_summary(
    document: Document, *, features_done: int = 0, features_failed: int = 0
) -> ProcessingSummary:
    """Compact summary for the Documents list tooltip. The metadata-derived fields are free from the
    row; ``features_done``/``features_failed`` come from the caller's one batched GROUP BY. Whether
    extraction failed cannot be derived without the rows here, so the OCR outcome is metadata-only
    ('done'/'not_needed'); the failed-feature count surfaces failure on the list instead."""
    meta: dict[str, Any] = document.metadata or {}
    extraction_method = str(meta.get("extraction_method", "") or "")
    return ProcessingSummary(
        extraction_method=extraction_method,
        ocr_outcome=_ocr_outcome(extraction_method, has_failed_extract=False),
        page_count=_as_int_or_none(meta.get("page_count")),
        normalized_from_mime=str(meta.get("normalized_from", "") or ""),
        status=document.status.value,
        features_done=features_done,
        features_failed=features_failed,
    )
