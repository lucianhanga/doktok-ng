"""Domain errors raised by adapters and handled in core (keeps core free of adapter specifics)."""

from __future__ import annotations


class DuplicateActiveDocumentError(Exception):
    """An active document with the same content hash already exists (unique-constraint conflict).

    Adapters translate the storage-level uniqueness violation into this domain error so the
    ingestion pipeline can mark the new copy as a duplicate instead of failing it.
    """


class RenderLimitExceededError(Exception):
    """A document cannot be rasterized within the renderer's memory bounds.

    Raised by PDF renderers when a page (or the whole document) would exceed the pixel/byte caps;
    the ingestion pipeline maps this to a terminal ``render_limit_exceeded`` failure instead of
    letting the worker OOM-crash and re-queue the same file forever (audit v2 D-01).
    """
