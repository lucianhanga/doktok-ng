"""Adapt the vendored GLiNER / NuNER refiners to doktokNG's ``EntityNerExtractor`` port.

GLiNER and NuNER are local span-extraction models (run via the ``gliner`` package). doktokNG's NER
port emits only PERSON / ORG / GPE occurrences, so this adapter asks the model for the matching
open-vocabulary labels (``person`` / ``organization`` / ``location``) and maps the spans back to the
``EntityType`` enum. Unlike the LLM adapters (which truncate the doc to a single prompt and report
zero offsets), this windows the whole document so the small-context model is not truncated, shifts
each span back to document coordinates, and de-duplicates on ``(type, normalized)``.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from doktok_contracts.media import ExtractedEntity
from doktok_contracts.schemas import EntityType

from .refiners import GLiNERNER, NuNERNER, RefinementConfig

# doktok NER emits PERSON / ORG / GPE. Request these open-vocabulary labels from the model and map
# the returned spans back to the enum (mirrors the LLM NER people/organizations/places -> enum map).
_LABEL_TO_TYPE: dict[str, EntityType] = {
    "person": EntityType.PERSON,
    "organization": EntityType.ORG,
    "location": EntityType.GPE,
}
_DEFAULT_LABELS: tuple[str, ...] = tuple(_LABEL_TO_TYPE)

# Parity with the LLM NER's ``_MAX_CHARS`` ceiling, so the benchmark compares the same input budget.
_MAX_CHARS = 12_000
# GLiNER / NuNER truncate at ~512 tokens; window the document so long docs are not silently cut.
_WINDOW_CHARS = 1_500


def _windows(text: str, size: int) -> Iterator[tuple[str, int]]:
    """Yield ``(chunk, start_offset)`` windows, breaking on whitespace to avoid splitting spans."""
    n = len(text)
    if n <= size:
        yield text, 0
        return
    i = 0
    while i < n:
        end = min(i + size, n)
        if end < n:
            brk = max(text.rfind(" ", i + size // 2, end), text.rfind("\n", i + size // 2, end))
            if brk > i:
                end = brk
        yield text[i:end], i
        i = end


class _RefinerNerExtractor:
    """Shared ``EntityNerExtractor`` adapter over a vendored refiner (GLiNER or NuNER)."""

    def __init__(
        self,
        refiner: Any,
        *,
        labels: Sequence[str] = _DEFAULT_LABELS,
        window_chars: int = _WINDOW_CHARS,
        max_chars: int = _MAX_CHARS,
    ) -> None:
        self._refiner = refiner
        self._labels = [str(label) for label in labels]
        self._window_chars = window_chars
        self._max_chars = max_chars

    def extract(self, text: str) -> list[ExtractedEntity]:
        text = text[: self._max_chars]
        seen: set[tuple[str, str]] = set()
        out: list[ExtractedEntity] = []
        for chunk, offset in _windows(text, self._window_chars):
            for ent in self._refiner.extract(chunk, self._labels):
                etype = _LABEL_TO_TYPE.get(str(ent.label).strip().lower())
                if etype is None:
                    continue
                name = str(ent.normalized or ent.text).strip()
                if not name:
                    continue
                key = (etype.value, name.casefold())
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    ExtractedEntity(
                        entity_text=str(ent.text),
                        entity_type=etype,
                        normalized_value=name,
                        start_offset=offset + int(ent.start),
                        end_offset=offset + int(ent.end),
                    )
                )
        return out


class GlinerEntityNerExtractor(_RefinerNerExtractor):
    """``EntityNerExtractor`` backed by GLiNER (default ``gliner-community/gliner_large-v2.5``)."""

    def __init__(
        self,
        model_name: str = "gliner-community/gliner_large-v2.5",
        *,
        config: RefinementConfig | None = None,
        device: str | None = None,
        model: Any = None,
        labels: Sequence[str] = _DEFAULT_LABELS,
        window_chars: int = _WINDOW_CHARS,
        max_chars: int = _MAX_CHARS,
        load_kwargs: dict[str, Any] | None = None,
    ) -> None:
        refiner = GLiNERNER(
            model_name,
            config=config or RefinementConfig(),
            device=device,
            model=model,
            load_kwargs=load_kwargs,
        )
        super().__init__(refiner, labels=labels, window_chars=window_chars, max_chars=max_chars)


class NuNerEntityNerExtractor(_RefinerNerExtractor):
    """``EntityNerExtractor`` backed by NuNER Zero (``numind/NuNER_Zero`` by default)."""

    def __init__(
        self,
        model_name: str = "numind/NuNER_Zero",
        *,
        config: RefinementConfig | None = None,
        device: str | None = None,
        model: Any = None,
        labels: Sequence[str] = _DEFAULT_LABELS,
        window_chars: int = _WINDOW_CHARS,
        max_chars: int = _MAX_CHARS,
        load_kwargs: dict[str, Any] | None = None,
    ) -> None:
        refiner = NuNERNER(
            model_name,
            config=config or RefinementConfig(),
            device=device,
            model=model,
            load_kwargs=load_kwargs,
        )
        super().__init__(refiner, labels=labels, window_chars=window_chars, max_chars=max_chars)
