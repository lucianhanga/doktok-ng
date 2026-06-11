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


@dataclass
class ExtractedTerm:
    """A significant lexical term (lexeme) and how often it occurs in a document."""

    term: str
    frequency: int


@dataclass
class ExtractedMetadata:
    """Raw enrichment fields from the LLM (validated/normalized in core). M6.2."""

    title: str
    document_date: str | None  # ISO 'YYYY-MM-DD' as produced by the model, or None for n/a
    location: str | None
    summary: str


@dataclass
class ExtractedTransaction:
    """A raw line item from a financial document (validated/normalized in core). M6.3."""

    raw_text: str
    date: str | None  # ISO 'YYYY-MM-DD' as produced by the model
    merchant: str | None  # merchant/payee name
    description: str | None
    amount: str | None  # e.g. "45.00"
    currency: str | None  # ISO 4217
    direction: str | None  # 'debit' | 'credit'
