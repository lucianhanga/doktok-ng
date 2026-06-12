"""Domain errors raised by adapters and handled in core (keeps core free of adapter specifics)."""

from __future__ import annotations


class DuplicateActiveDocumentError(Exception):
    """An active document with the same content hash already exists (unique-constraint conflict).

    Adapters translate the storage-level uniqueness violation into this domain error so the
    ingestion pipeline can mark the new copy as a duplicate instead of failing it.
    """
