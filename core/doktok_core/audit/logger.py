"""Helper to record activity/audit events without ever crashing the caller.

Audit best practice is an immutable, structured trail; but a failure to write an audit row must not
break ingestion, so write errors are logged and swallowed here.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from doktok_contracts.ports import AuditLogRepository
from doktok_contracts.schemas import AuditEvent, AuditEventType

logger = logging.getLogger("doktok.audit")

# Lifecycle phase + default severity + human description for the legacy six event types, so the
# enhanced activity table (document_activity) is populated meaningfully even before the full
# lifecycle capture lands. New event types pass phase/severity/description explicitly.
_EVENT_DEFAULTS: dict[str, tuple[str, str, str]] = {
    AuditEventType.DOCUMENT_RECEIVED.value: ("intake", "info", "Document received for processing"),
    AuditEventType.DOCUMENT_IDENTIFIED.value: (
        "intake",
        "info",
        "Document identified and accepted",
    ),
    AuditEventType.DOCUMENT_DUPLICATE.value: ("intake", "info", "Duplicate of existing document"),
    AuditEventType.DOCUMENT_ACTIVATED.value: ("index", "info", "Document activated and searchable"),
    AuditEventType.DOCUMENT_QUARANTINED.value: ("intake", "warning", "Document quarantined"),
    AuditEventType.DOCUMENT_FAILED.value: ("intake", "error", "Document processing failed"),
    AuditEventType.FEATURE_COMPLETED.value: ("enrich", "info", "Feature completed"),
    AuditEventType.FEATURE_FAILED.value: ("enrich", "error", "Feature failed"),
    AuditEventType.FEATURE_RETRIED.value: ("user", "info", "Feature re-queued by user"),
    AuditEventType.DOCUMENT_ROTATED.value: ("user", "info", "Document rotated and re-ingested"),
    AuditEventType.DOCUMENT_REINGESTED.value: ("user", "info", "Document re-ingested by user"),
    AuditEventType.DOCUMENT_DELETED.value: ("delete", "info", "Document deleted by user"),
    AuditEventType.DOCUMENT_VIEWED.value: ("view", "info", "Document viewed"),
}


def record_activity(
    repo: AuditLogRepository,
    tenant_id: str,
    event_type: AuditEventType,
    *,
    actor: str = "worker",
    actor_kind: str = "worker",
    document_id: str | None = None,
    job_id: str | None = None,
    details: dict[str, Any] | None = None,
    phase: str | None = None,
    severity: str | None = None,
    description: str | None = None,
    record_kind: str | None = None,
    record_id: str | None = None,
    doc_filename: str | None = None,
    doc_title: str | None = None,
) -> None:
    default_phase, default_severity, default_description = _EVENT_DEFAULTS.get(
        event_type.value, ("", "info", "")
    )
    try:
        repo.record(
            AuditEvent(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                event_type=event_type.value,
                actor=actor,
                actor_kind=actor_kind,
                document_id=document_id,
                job_id=job_id,
                timestamp=datetime.now(UTC),
                metadata=details or {},
                phase=phase if phase is not None else default_phase,
                severity=severity if severity is not None else default_severity,
                description=description if description is not None else default_description,
                record_kind=record_kind,
                record_id=record_id,
                doc_filename=doc_filename,
                doc_title=doc_title,
            )
        )
    except Exception:  # noqa: BLE001 - audit write failures must not break ingestion
        logger.warning("failed to record audit event %s", event_type.value, exc_info=True)
