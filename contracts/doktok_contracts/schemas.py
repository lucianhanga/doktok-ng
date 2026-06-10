"""Contracts-first data schemas for DokTok NG.

These Pydantic models are the canonical shapes for documents, ingestion jobs, chunks, entities,
artifacts, and audit events. They are defined in the contracts package so that core logic and all
adapters depend on the same shapes. See docs/architecture and brief sections 14-16.

Every tenant-owned entity carries a ``tenant_id`` (ADR-0007). Tenant identity always comes from the
authenticated token, never from request input (ADR-0008).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    """Ingestion job state machine (ADR-0004)."""

    QUEUED = "queued"
    DETECTING = "detecting"
    HASHING = "hashing"
    NORMALIZING = "normalizing"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    ACTIVATING = "activating"
    ACTIVE = "active"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class DocumentStatus(StrEnum):
    PROCESSING = "processing"
    ACTIVE = "active"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class SecurityDecision(StrEnum):
    """Outcome of evaluating an ingested file against the security policy."""

    ALLOW = "allow"
    QUARANTINE = "quarantine"
    REJECT = "reject"


class AuditEventType(StrEnum):
    """Controlled vocabulary for the immutable activity/audit trail (ADR-0006)."""

    DOCUMENT_RECEIVED = "document.received"
    DOCUMENT_IDENTIFIED = "document.identified"
    DOCUMENT_ACTIVATED = "document.activated"
    DOCUMENT_QUARANTINED = "document.quarantined"
    DOCUMENT_FAILED = "document.failed"


class EntityType(StrEnum):
    PERSON = "PERSON"
    ORG = "ORG"
    GPE = "GPE"
    LOCATION = "LOCATION"
    DATE = "DATE"
    EMAIL = "EMAIL"
    URL = "URL"
    MONEY = "MONEY"
    DOCUMENT_ID = "DOCUMENT_ID"
    INVOICE_ID = "INVOICE_ID"
    CONTRACT_ID = "CONTRACT_ID"
    CUSTOM_TOKEN = "CUSTOM_TOKEN"


class TenantContext(BaseModel):
    """The authenticated caller's tenant (ADR-0008)."""

    tenant_id: str


class Document(BaseModel):
    id: str
    tenant_id: str
    current_version_id: str | None = None
    sha256: str
    original_filename: str
    detected_mime: str | None = None
    title: str | None = None
    status: DocumentStatus = DocumentStatus.PROCESSING
    storage_path: str | None = None
    created_at: datetime
    activated_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentVersion(BaseModel):
    id: str
    tenant_id: str
    document_id: str
    version_number: int
    sha256: str
    created_at: datetime
    extraction_method: str | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)


class IngestionJob(BaseModel):
    id: str
    tenant_id: str
    document_id: str | None = None
    source_path: str
    status: JobStatus = JobStatus.QUEUED
    detected_mime: str | None = None
    sha256: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentPage(BaseModel):
    id: str
    tenant_id: str
    document_id: str
    version_id: str
    page_number: int
    text: str
    layout: dict[str, Any] = Field(default_factory=dict)
    extraction_method: str | None = None
    ocr_confidence: float | None = None


class ChunkMetadata(BaseModel):
    document_id: str
    version_id: str
    chunk_id: str
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    source_offsets: dict[str, Any] | None = None
    extraction_method: str | None = None
    ocr_confidence: float | None = None
    token_count: int | None = None


class DocumentChunk(BaseModel):
    id: str
    tenant_id: str
    document_id: str
    version_id: str
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    text: str
    token_count: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentEntity(BaseModel):
    id: str
    tenant_id: str
    document_id: str
    version_id: str
    chunk_id: str | None = None
    entity_text: str
    entity_type: EntityType
    normalized_value: str | None = None
    frequency: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentArtifact(BaseModel):
    id: str
    tenant_id: str
    document_id: str
    version_id: str
    artifact_type: str
    storage_path: str
    mime_type: str | None = None
    sha256: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditEvent(BaseModel):
    id: str
    tenant_id: str
    event_type: str
    actor: str
    document_id: str | None = None
    job_id: str | None = None
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class HealthStatus(BaseModel):
    """Backend health/status payload returned by GET /health."""

    status: str
    service: str
    version: str
    environment: str
