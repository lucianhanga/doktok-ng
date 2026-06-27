"""Lightweight media value types used by OCR ports (plain dataclasses for raw-bytes efficiency)."""

from __future__ import annotations

from dataclasses import dataclass, field

from doktok_contracts.schemas import EntityType


@dataclass
class OcrTextLine:
    """One recognized line + its axis-aligned bbox in rendered-image pixels. The searchable PDF
    page is created at the image's pixel size, so these coordinates are also its PDF points - which
    makes the positioned text layer DPI-independent by construction."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class OcrPageResult:
    """Text recognized from a single page image, with optional confidence (0-1), per-line boxes
    (empty when the engine does not report them, e.g. the Ollama vision OCR path), and the source
    image's pixel size (the coordinate space the boxes live in; 0 when unknown)."""

    text: str
    confidence: float | None = None
    lines: list[OcrTextLine] = field(default_factory=list)
    width: int = 0
    height: int = 0
    # Clockwise rotation (0/90/180/270) the Enhanced 4-way vote applied to upright the page; the
    # text/lines/width/height above are already in that rotated frame.
    rotation: int = 0


@dataclass
class RenderedPage:
    """A page image plus its text (and optional per-line boxes) to assemble a searchable PDF."""

    image_png: bytes
    text: str
    lines: list[OcrTextLine] = field(default_factory=list)
    # Clockwise rotation to apply to image_png so it matches the (already-rotated) line boxes.
    rotation: int = 0


@dataclass
class PageLayout:
    """Per-page OCR geometry persisted to content.json so coordinates are interpretable: the image
    pixel size + render DPI the line boxes are expressed in (``dpi`` None for source images)."""

    width_px: int
    height_px: int
    dpi: int | None
    lines: list[OcrTextLine]


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


@dataclass
class ExtractedRelation:
    """A raw relation triple from one document window (pre-resolution). KAG Phase 2."""

    subject: str  # entity name as extracted (must match a document entity)
    predicate: str  # one of the controlled predicates
    object: str  # entity name as extracted (must match a document entity)
    subject_type: str  # ORG / PERSON / GPE / LOCATION
    object_type: str  # ORG / PERSON / GPE / LOCATION
    evidence: str  # verbatim source sentence(s), max ~250 chars


@dataclass
class LlmUsage:
    """Token counts and timing for one LLM call (M8). Exact when the provider reports them; else
    estimated (character-based) with ``estimated=True``. ``wall_ms`` is measured by the provider
    around the call; ``eval_ms`` is the model's own generation time when reported (None otherwise).

    For Ollama, reasoning and answer tokens are not reported separately, so they are split from the
    total ``eval_count`` by output character ratio (an estimate). OpenAI reasoning models report
    ``reasoning_tokens`` exactly.
    """

    prompt_tokens: int = 0
    answer_tokens: int = 0
    reasoning_tokens: int = 0
    wall_ms: int = 0
    eval_ms: int | None = None
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.answer_tokens + self.reasoning_tokens
