"""Document enrichment (M6.2): validate/normalize LLM-extracted metadata + categories."""

from doktok_core.enrichment.categories import (
    MAX_CATEGORIES_PER_DOCUMENT,
    MAX_CATEGORIES_PER_TENANT,
    normalize_category,
)
from doktok_core.enrichment.metadata import NormalizedMetadata, normalize_metadata

__all__ = [
    "MAX_CATEGORIES_PER_DOCUMENT",
    "MAX_CATEGORIES_PER_TENANT",
    "NormalizedMetadata",
    "normalize_category",
    "normalize_metadata",
]
