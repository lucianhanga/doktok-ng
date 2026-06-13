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


@dataclass
class ProjectionResult:
    """Output of fitting the embedding map (M7.2): per-dimension coords + a cluster id per vector.

    ``coords[d]`` holds one d-length coordinate per input vector (same order as the input).
    ``clusters`` holds one HDBSCAN cluster id per input vector (-1 = noise); the same id is used for
    every dimension so colors agree across 2D/3D.
    """

    coords: dict[int, list[list[float]]]
    clusters: list[int]


@dataclass
class ChatChunk:
    """One streamed piece of a chat response (M6.4): an answer token or a reasoning/thinking token.

    ``kind`` is "answer" (user-visible content) or "reasoning" (the model's thinking, shown in a
    collapsible panel). Models that don't separate reasoning only ever emit "answer" chunks.
    """

    kind: str  # "answer" | "reasoning"
    text: str
