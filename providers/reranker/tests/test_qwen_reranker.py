"""Ranking-logic tests for QwenReranker that never load the heavy model (bypass __init__)."""

from __future__ import annotations

import pytest
from doktok_contracts.schemas import SearchHit
from doktok_provider_reranker.qwen import QwenReranker


def _hits(n: int) -> list[SearchHit]:
    return [
        SearchHit(document_id=str(i), chunk_id=str(i), snippet=f"s{i}", text=f"t{i}", score=1.0)
        for i in range(n)
    ]


def _reranker(scores: list[float]) -> QwenReranker:
    r = QwenReranker.__new__(QwenReranker)  # skip model loading
    r._scores = lambda query, docs: scores  # type: ignore[method-assign]
    return r


def test_rerank_orders_by_score_and_truncates() -> None:
    out = _reranker([0.1, 0.9, 0.5]).rerank("q", _hits(3), top_k=2)
    assert [h.document_id for h in out] == ["1", "2"]  # highest score first, capped to top_k


def test_rerank_short_circuits_for_one_hit() -> None:
    # No model call for <=1 hit.
    assert [h.document_id for h in QwenReranker.rerank(object(), "q", _hits(1), top_k=5)] == ["0"]  # type: ignore[arg-type]


def test_rerank_falls_back_to_input_order_on_scoring_error() -> None:
    r = QwenReranker.__new__(QwenReranker)

    def _boom(query: str, docs: list[str]) -> list[float]:
        raise RuntimeError("model exploded")

    r._scores = _boom  # type: ignore[method-assign]
    out = r.rerank("q", _hits(3), top_k=3)
    assert [h.document_id for h in out] == ["0", "1", "2"]  # original retrieval order preserved


def test_rerank_attaches_rerank_score_highest_first() -> None:
    # rerank_score equals the yes-probability; top hit has the highest score.
    out = _reranker([0.1, 0.9, 0.5]).rerank("q", _hits(3), top_k=3)
    assert [h.document_id for h in out] == ["1", "2", "0"]
    assert out[0].rerank_score == pytest.approx(0.9)
    assert out[1].rerank_score == pytest.approx(0.5)
    assert out[2].rerank_score == pytest.approx(0.1)


def test_rerank_score_none_on_scoring_failure() -> None:
    # The failure path must not set rerank_score (hits keep their original rerank_score=None).
    r = QwenReranker.__new__(QwenReranker)

    def _boom(query: str, docs: list[str]) -> list[float]:
        raise RuntimeError("boom")

    r._scores = _boom  # type: ignore[method-assign]
    out = r.rerank("q", _hits(3), top_k=3)
    assert all(h.rerank_score is None for h in out)


def test_rerank_score_none_for_single_hit_short_circuit() -> None:
    # The <=1 short-circuit path must leave rerank_score as None (no scoring ran).
    out = QwenReranker.rerank(object(), "q", _hits(1), top_k=5)  # type: ignore[arg-type]
    assert len(out) == 1
    assert out[0].rerank_score is None
