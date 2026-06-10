"""Lightweight media value types used by OCR ports (plain dataclasses for raw-bytes efficiency)."""

from __future__ import annotations

from dataclasses import dataclass


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
