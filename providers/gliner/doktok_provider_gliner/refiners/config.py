from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

GazetteerValue = Iterable[str] | Mapping[str, Iterable[str]]


@dataclass
class RefinementConfig:
    """
    Configuration shared by the GLiNER and NuNER wrappers.

    labels are compared case-insensitively after stripping whitespace.
    Gazetteers can be passed as either:
      {"organization": ["OpenAI", "Microsoft"]}
    or:
      {"organization": {"OpenAI": ["Open AI", "OpenAI Inc."], "Microsoft": ["MSFT"]}}
    """

    default_threshold: float = 0.50
    label_thresholds: dict[str, float] = field(default_factory=dict)
    low_confidence_margin: float = 0.15

    # Add deterministic rule-based entities in addition to model candidates.
    enable_regex_rules: bool = True
    enable_gazetteers: bool = True
    enable_fuzzy_gazetteers: bool = True

    # Fuzzy matching is intentionally conservative. Raise for high-precision workloads.
    fuzzy_threshold: int = 92
    max_fuzzy_aliases: int = 2_000
    max_fuzzy_text_chars: int = 20_000

    # Conflict handling.
    prefer_longer_spans: bool = True
    score_close_delta: float = 0.08
    label_priority: list[str] = field(
        default_factory=lambda: [
            "email",
            "phone",
            "url",
            "currency",
            "money",
            "date",
            "datetime",
            "id",
            "identifier",
            "person",
            "organization",
            "company",
            "location",
        ]
    )

    # Labels requested by your project may differ from the regex names below.
    # Example: {"email": "EMAIL_ADDRESS", "phone": "PHONE_NUMBER"}
    regex_label_map: dict[str, str] = field(
        default_factory=lambda: {
            "email": "email",
            "phone": "phone",
            "url": "url",
            "date": "date",
            "currency": "currency",
            "id": "id",
        }
    )

    # Treat these labels as having strong validators. If a model predicts one of these labels
    # but the text does not validate, the candidate is dropped.
    validate_regex_labels: bool = True

    # Whether to keep model candidates below threshold in ExtractionResult.low_confidence.
    collect_low_confidence: bool = True

    # Trim these characters from model spans.
    trim_chars: str = " \t\n\r\f\v.,;:!?()[]{}<>\"'`“”‘’"

    # User dictionaries.
    gazetteers: dict[str, GazetteerValue] = field(default_factory=dict)

    def threshold_for(self, label: str) -> float:
        return self.label_thresholds.get(label_key(label), self.default_threshold)

    def candidate_threshold_floor(self) -> float:
        """Lowest score worth asking the model for, so post-processing can rescue edge cases."""
        thresholds = [self.default_threshold, *self.label_thresholds.values()]
        return max(0.01, min(thresholds) - self.low_confidence_margin)


def label_key(label: str) -> str:
    return " ".join(str(label).strip().lower().split())


def canonical_label(label: str, labels: Iterable[str] | None = None) -> str:
    """Return the caller's casing when the label exists in the requested label list."""
    normalized = label_key(label)
    if labels:
        for existing in labels:
            if label_key(existing) == normalized:
                return str(existing)
    return normalized
