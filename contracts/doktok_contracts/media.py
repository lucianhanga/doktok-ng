"""Lightweight media value types used by OCR ports (plain dataclasses for raw-bytes efficiency)."""

from __future__ import annotations

from dataclasses import dataclass

from doktok_contracts.schemas import EntityType


@dataclass
class OcrPageResult:
    """Text recognized from a single page image, with optional confidence (0-1)."""

    text: str
    confidence: float | None = None


@dataclass
class RenderedPage:
    """A page image plus its text, used to assemble a searchable PDF."""

    image_png: bytes
    text: str


@dataclass
class TextChunk:
    """A deterministic slice of extracted text, ready to embed and index."""

    text: str
    token_count: int
    start_offset: int
    end_offset: int


@dataclass
class ExtractedEntity:
    """A single entity occurrence found in text (before tenant/document association)."""

    entity_text: str
    entity_type: EntityType
    normalized_value: str
    start_offset: int
    end_offset: int
