"""File modality handling."""

from doktok_modalities_files.extractors import (
    DirectTextExtractor,
    PyMuPdfClassifier,
    PyMuPdfTextExtractor,
)
from doktok_modalities_files.mime import LibmagicMimeDetector
from doktok_modalities_files.normalize import GotenbergNormalizer
from doktok_modalities_files.render import (
    PyMuPdfRenderer,
    PyMuPdfThumbnailer,
    SearchablePdfBuilder,
    rotate_source,
)

__version__ = "0.2.0"

__all__ = [
    "DirectTextExtractor",
    "GotenbergNormalizer",
    "LibmagicMimeDetector",
    "PyMuPdfClassifier",
    "PyMuPdfRenderer",
    "PyMuPdfTextExtractor",
    "PyMuPdfThumbnailer",
    "SearchablePdfBuilder",
    "rotate_source",
]
