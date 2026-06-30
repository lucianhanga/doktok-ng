"""Small benchmarking helpers for KG extraction outputs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .schemas import KGTriple, normalize_label


@dataclass(slots=True)
class TripleMetrics:
    precision: float
    recall: float
    f1: float
    true_positives: int
    false_positives: int
    false_negatives: int
    details: dict = field(default_factory=dict)


def triple_key(triple: KGTriple | dict) -> tuple[str, str, str]:
    if isinstance(triple, KGTriple):
        return (triple.subject_id, normalize_label(triple.predicate), triple.object_id)
    return (
        str(triple["subject_id"]),
        normalize_label(str(triple["predicate"])),
        str(triple["object_id"]),
    )


def evaluate_triples(
    predicted: Sequence[KGTriple | dict], gold: Sequence[KGTriple | dict]
) -> TripleMetrics:
    pred_keys = {triple_key(t) for t in predicted}
    gold_keys = {triple_key(t) for t in gold}
    tp = len(pred_keys & gold_keys)
    fp = len(pred_keys - gold_keys)
    fn = len(gold_keys - pred_keys)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return TripleMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        details={
            "missing": sorted(gold_keys - pred_keys),
            "extra": sorted(pred_keys - gold_keys),
        },
    )


__all__ = ["TripleMetrics", "triple_key", "evaluate_triples"]
