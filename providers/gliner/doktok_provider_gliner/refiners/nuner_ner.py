from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .config import RefinementConfig
from .refinement import FallbackCallable, RefinementPipeline
from .types import Entity, ExtractionResult


class NuNERNER:
    """
    NuNER-backed NER extractor with the same post-processing pipeline as GLiNERNER.

    NuNER Zero model cards recommend lower-cased labels, so this wrapper lower-cases
    labels for the model call while preserving the caller's original label casing in output.
    """

    def __init__(
        self,
        model_name: str = "numind/NuNER_Zero",
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
                    "NuNER Zero uses the GLiNER interface. Install with: pip install gliner"
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
        candidate_threshold = (
            threshold if threshold is not None else self.config.candidate_threshold_floor()
        )

        # NuNER expects lower-cased labels. The refinement layer maps back to caller labels.
        model_labels = [str(label).lower() for label in labels]
        raw = self.model.predict_entities(text, model_labels, threshold=candidate_threshold)
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
