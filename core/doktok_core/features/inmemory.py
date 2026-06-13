"""In-memory feature ledger for tests and local/dev runs (ADR-0009)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from doktok_contracts.schemas import DocumentFeature, FeatureStatus


class InMemoryFeatureRepository:
    def __init__(self, active: dict[str, list[str]] | None = None) -> None:
        self.rows: list[DocumentFeature] = []
        # tenant_id -> active document ids, used by ensure_for_active (DB version joins documents).
        self.active = active or {}

    def _find(self, tenant_id: str, document_id: str, feature: str) -> DocumentFeature | None:
        for row in self.rows:
            if (
                row.tenant_id == tenant_id
                and row.document_id == document_id
                and row.feature == feature
            ):
                return row
        return None

    def record_done(
        self, tenant_id: str, document_id: str, feature: str, feature_version: int
    ) -> None:
        now = datetime.now()
        row = self._find(tenant_id, document_id, feature)
        if row is None:
            self.rows.append(
                DocumentFeature(
                    id=uuid.uuid4().hex,
                    tenant_id=tenant_id,
                    document_id=document_id,
                    feature=feature,
                    feature_version=feature_version,
                    status=FeatureStatus.DONE,
                    completed_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.status = FeatureStatus.DONE
            row.feature_version = feature_version
            row.attempts = 0
            row.last_error = None
            row.completed_at = now

    def ensure_for_active(self, tenant_id: str, features: list[tuple[str, int]]) -> int:
        affected = 0
        now = datetime.now()
        for document_id in self.active.get(tenant_id, []):
            for name, version in features:
                row = self._find(tenant_id, document_id, name)
                if row is None:
                    self.rows.append(
                        DocumentFeature(
                            id=uuid.uuid4().hex,
                            tenant_id=tenant_id,
                            document_id=document_id,
                            feature=name,
                            feature_version=version,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    affected += 1
                elif row.status is FeatureStatus.DONE and row.feature_version < version:
                    row.status = FeatureStatus.PENDING
                    row.feature_version = version
                    row.attempts = 0
                    affected += 1
            # 'extract' is the inline activation marker (the 'text' badge), not a reconciler
            # processor, so the loop never seeds it. An active document IS extracted, so ensure a
            # done extract row exists for any active doc missing it - the badge self-heals.
            if self._find(tenant_id, document_id, "extract") is None:
                self.rows.append(
                    DocumentFeature(
                        id=uuid.uuid4().hex,
                        tenant_id=tenant_id,
                        document_id=document_id,
                        feature="extract",
                        feature_version=1,
                        status=FeatureStatus.DONE,
                        completed_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
                affected += 1
        return affected

    def seed_for_document(
        self, tenant_id: str, document_id: str, stages: list[tuple[str, int]]
    ) -> int:
        now = datetime.now()
        affected = 0
        for name, version in stages:
            if self._find(tenant_id, document_id, name) is None:
                self.rows.append(
                    DocumentFeature(
                        id=uuid.uuid4().hex,
                        tenant_id=tenant_id,
                        document_id=document_id,
                        feature=name,
                        feature_version=version,
                        created_at=now,
                        updated_at=now,
                    )
                )
                affected += 1
        return affected

    def claim_next(
        self,
        tenant_id: str,
        *,
        now: datetime,
        reclaim_before: datetime,
        dependencies: Sequence[tuple[str, str]] = (),
    ) -> DocumentFeature | None:
        prereqs: dict[str, set[str]] = {}
        for feature, prereq in dependencies:
            prereqs.setdefault(feature, set()).add(prereq)

        def ready(row: DocumentFeature) -> bool:
            # Every prerequisite of this feature must have a 'done' row on the same document.
            for prereq in prereqs.get(row.feature, set()):
                if not any(
                    r.tenant_id == tenant_id
                    and r.document_id == row.document_id
                    and r.feature == prereq
                    and r.status is FeatureStatus.DONE
                    for r in self.rows
                ):
                    return False
            return True

        for row in self.rows:
            if row.tenant_id != tenant_id:
                continue
            due = (
                row.status is FeatureStatus.PENDING
                or (
                    row.status is FeatureStatus.FAILED
                    and row.attempts < row.max_attempts
                    and (row.next_attempt_at is None or row.next_attempt_at <= now)
                )
                or (
                    row.status is FeatureStatus.RUNNING
                    and row.last_attempt_at is not None
                    and row.last_attempt_at < reclaim_before
                )
            )
            if due and ready(row):
                row.status = FeatureStatus.RUNNING
                row.attempts += 1
                row.last_attempt_at = now
                return row.model_copy(deep=True)
        return None

    def mark_done(self, feature_id: str, *, feature_version: int) -> None:
        for row in self.rows:
            if row.id == feature_id:
                row.status = FeatureStatus.DONE
                row.feature_version = feature_version
                row.last_error = None
                row.completed_at = datetime.now()

    def mark_failed(self, feature_id: str, *, error: str, next_attempt_at: datetime) -> None:
        for row in self.rows:
            if row.id == feature_id:
                row.status = FeatureStatus.FAILED
                row.last_error = error
                row.next_attempt_at = next_attempt_at

    def list_for_document(self, tenant_id: str, document_id: str) -> list[DocumentFeature]:
        return [
            row.model_copy(deep=True)
            for row in self.rows
            if row.tenant_id == tenant_id and row.document_id == document_id
        ]

    def list_for_tenant(self, tenant_id: str, *, limit: int = 2000) -> list[DocumentFeature]:
        rows = [r.model_copy(deep=True) for r in self.rows if r.tenant_id == tenant_id]
        rows.sort(key=lambda r: (r.document_id, r.feature))
        return rows[:limit]

    def list_for_documents(self, tenant_id: str, document_ids: list[str]) -> list[DocumentFeature]:
        wanted = set(document_ids)
        rows = [
            r.model_copy(deep=True)
            for r in self.rows
            if r.tenant_id == tenant_id and r.document_id in wanted
        ]
        rows.sort(key=lambda r: (r.document_id, r.feature))
        return rows

    def reset(self, tenant_id: str, document_id: str, feature: str) -> bool:
        row = self._find(tenant_id, document_id, feature)
        if row is None:
            return False
        row.status = FeatureStatus.PENDING
        row.attempts = 0
        row.last_error = None
        row.next_attempt_at = None
        return True

    def requeue_running(self, tenant_id: str) -> int:
        n = 0
        for row in self.rows:
            if row.tenant_id == tenant_id and row.status is FeatureStatus.RUNNING:
                row.status = FeatureStatus.PENDING
                row.last_attempt_at = None
                n += 1
        return n
