"""File modality handling."""

from doktok_modalities_files.extractors import (
    DirectTextExtractor,
    PyMuPdfClassifier,
    PyMuPdfTextExtractor,
)
from doktok_modalities_files.mime import LibmagicMimeDetector
from doktok_modalities_files.render import (
    PyMuPdfRenderer,
    PyMuPdfThumbnailer,
    SearchablePdfBuilder,
)

__version__ = "0.0.0"

__all__ = [
    "DirectTextExtractor",
    "LibmagicMimeDetector",
    "PyMuPdfClassifier",
    "PyMuPdfRenderer",
    "PyMuPdfTextExtractor",
    "PyMuPdfThumbnailer",
    "SearchablePdfBuilder",
]
