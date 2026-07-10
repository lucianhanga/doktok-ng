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
from doktok_contracts.schemas import AuditEvent, AuditEventType, TenantContext

logger = logging.getLogger("doktok.audit")


def actor_identity(tenant: TenantContext) -> str:
    """The audit actor id for an authenticated request (#560): the authenticated user's id when the
    caller logged in (session JWT / user-bound token), else the tenant id - a tenant-scoped operator
    token has no user identity. Pair with ``actor_kind="user"``; worker/system actors are recorded
    with their own literal actor + kind and are never routed through here, so they stay
    distinguishable in the trail."""
    return tenant.user_id or tenant.tenant_id


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
    AuditEventType.SETTINGS_CHANGED.value: ("user", "info", "Settings changed"),
    AuditEventType.SERVICE_STARTED.value: ("system", "info", "Service started"),
    AuditEventType.BACKUP_COMPLETED.value: ("system", "info", "Backup completed"),
    AuditEventType.BACKUP_FAILED.value: ("system", "error", "Backup failed"),
    AuditEventType.DRILL_COMPLETED.value: ("system", "info", "Restore drill completed"),
    AuditEventType.RESTORE_PREVIEWED.value: ("user", "info", "Portable restore previewed"),
    AuditEventType.RESTORE_REQUESTED.value: ("user", "warning", "Portable restore requested"),
    AuditEventType.RESTORE_COMPLETED.value: ("system", "info", "Portable restore completed"),
    AuditEventType.RESTORE_FAILED.value: ("system", "error", "Portable restore failed"),
    AuditEventType.ENTITY_MERGED.value: ("user", "info", "Entity merged"),
    AuditEventType.ENTITY_SPLIT.value: ("user", "info", "Entity split"),
    AuditEventType.TENANT_CREATED.value: ("admin", "info", "Tenant created"),
    AuditEventType.USER_CREATED.value: ("admin", "info", "User created"),
    AuditEventType.USER_ROLE_CHANGED.value: ("admin", "warning", "User role changed"),
    AuditEventType.USER_PASSWORD_RESET.value: ("admin", "warning", "User password reset"),
    AuditEventType.API_TOKEN_ISSUED.value: ("admin", "warning", "API token issued"),
    AuditEventType.API_TOKEN_REVOKED.value: ("admin", "info", "API token revoked"),
    AuditEventType.USER_INVITED.value: ("admin", "info", "User invited"),
    AuditEventType.USER_DEACTIVATED.value: ("admin", "warning", "User deactivated"),
    AuditEventType.USER_REACTIVATED.value: ("admin", "info", "User reactivated"),
    AuditEventType.USER_INVITE_ACCEPTED.value: ("user", "info", "Invitation accepted"),
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
    event_id: str | None = None,
) -> None:
    """Record one activity row. ``event_id`` lets the caller supply a deterministic id so repeated
    near-identical events (e.g. a document view fired twice by React StrictMode) collapse to one
    row via the repository's insert-if-absent semantics; otherwise a random id is used."""
    default_phase, default_severity, default_description = _EVENT_DEFAULTS.get(
        event_type.value, ("", "info", "")
    )
    try:
        repo.record(
            AuditEvent(
                id=event_id or uuid.uuid4().hex,
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
