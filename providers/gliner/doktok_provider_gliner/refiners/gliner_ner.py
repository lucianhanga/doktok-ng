from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .config import RefinementConfig
from .refinement import FallbackCallable, RefinementPipeline
from .types import Entity, ExtractionResult


class GLiNERNER:
    """
    GLiNER-backed NER extractor with deterministic post-processing.

    Example:
        extractor = GLiNERNER()
        result = extractor.extract(
            "Contact jane@acme.com at Acme GmbH on 2026-07-01.",
            labels=["email", "organization", "date"],
            return_result=True,
        )
    """

    def __init__(
        self,
        model_name: str = "gliner-community/gliner_large-v2.5",
        *,
        config: RefinementConfig | None = None,
        device: str | None = None,
        model: Any = None,
        load_kwargs: dict[str, Any] | None = None,
    ):
        self.model_name = model_name
        self.config = config or RefinementConfig()
        self.pipeline = RefinementPipeline(self.config)

        if model is not None:
            self.model = model
        else:
            try:
                from gliner import GLiNER
            except ImportError as exc:  # pragma: no cover - import guidance
                raise ImportError(
                    "GLiNER is not installed. Install with: pip install gliner"
                ) from exc
            self.model = GLiNER.from_pretrained(model_name, **(load_kwargs or {}))

        if device and hasattr(self.model, "to"):
            self.model.to(device)

    def extract(
        self,
        text: str,
        labels: Sequence[str],
        *,
        threshold: float | None = None,
        return_result: bool = False,
        frontier_fallback: FallbackCallable | None = None,
    ) -> list[Entity] | ExtractionResult:
        """
        Extract entities for arbitrary labels.

        threshold controls the candidate generation floor passed to GLiNER.
        Final acceptance still uses config.default_threshold and config.label_thresholds.
        """
        candidate_threshold = (
            threshold if threshold is not None else self.config.candidate_threshold_floor()
        )
        raw = self.model.predict_entities(text, list(labels), threshold=candidate_threshold)
        result = self.pipeline.refine(text, list(labels), raw, frontier_fallback=frontier_fallback)
        return result if return_result else result.entities

    def batch_extract(
        self,
        texts: Sequence[str],
        labels: Sequence[str],
        *,
        threshold: float | None = None,
        return_result: bool = False,
    ) -> list[list[Entity]] | list[ExtractionResult]:
        outputs = [
            self.extract(text, labels, threshold=threshold, return_result=return_result)
            for text in texts
        ]
        return outputs  # type: ignore[return-value]
