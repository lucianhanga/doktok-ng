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


def record_activity(
    repo: AuditLogRepository,
    tenant_id: str,
    event_type: AuditEventType,
    *,
    actor: str = "worker",
    document_id: str | None = None,
    job_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    try:
        repo.record(
            AuditEvent(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                event_type=event_type.value,
                actor=actor,
                document_id=document_id,
                job_id=job_id,
                timestamp=datetime.now(UTC),
                metadata=details or {},
            )
        )
    except Exception:  # noqa: BLE001 - audit write failures must not break ingestion
        logger.warning("failed to record audit event %s", event_type.value, exc_info=True)
