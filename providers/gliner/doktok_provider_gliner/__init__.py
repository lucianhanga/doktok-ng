"""Local GLiNER / NuNER NER provider for doktokNG (experimental, opt-in).

Wraps the vendored ``refiners`` package (deterministic post-processing over GLiNER / NuNER span
candidates) and adapts it to the ``EntityNerExtractor`` port. The heavy ``gliner`` runtime is an
optional extra (``providers/gliner[engine]``, or ``make ner-models``); importing this package is
light, the model loads only when an extractor is instantiated.
"""

from __future__ import annotations

from .adapter import GlinerEntityNerExtractor, NuNerEntityNerExtractor
from .refiners import Entity, ExtractionResult, RefinementConfig
from .relation_adapter import GlinerRelexRelationExtractor

__version__ = "0.1.0"

__all__ = [
    "GlinerEntityNerExtractor",
    "NuNerEntityNerExtractor",
    "GlinerRelexRelationExtractor",
    "RefinementConfig",
    "Entity",
    "ExtractionResult",
]
