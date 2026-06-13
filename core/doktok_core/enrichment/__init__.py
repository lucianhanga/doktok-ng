"""Document enrichment (M6.2): validate/normalize LLM-extracted metadata + categories."""

from doktok_core.enrichment.categories import (
    MAX_CATEGORIES_PER_DOCUMENT,
    MAX_CATEGORIES_PER_TENANT,
    normalize_category,
)
from doktok_core.enrichment.metadata import NormalizedMetadata, normalize_metadata
from doktok_core.enrichment.unidentifiable import detect_unidentifiable

__all__ = [
    "MAX_CATEGORIES_PER_DOCUMENT",
    "MAX_CATEGORIES_PER_TENANT",
    "NormalizedMetadata",
    "detect_unidentifiable",
    "normalize_category",
    "normalize_metadata",
]
