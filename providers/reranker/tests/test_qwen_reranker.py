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


def test_init_loads_only_from_local_cache_never_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chat request must never trigger a multi-GB model download: __init__ loads with
    local_files_only=True so a model that is not on disk raises immediately and the backend
    falls back to the LLM listwise reranker."""
    import sys
    import types

    calls: dict[str, dict[str, object]] = {}

    class _FakeTokenizer:
        def convert_tokens_to_ids(self, token: str) -> int:
            return 1

        def __call__(self, text: str, add_special_tokens: bool = False) -> dict[str, list[int]]:
            return {"input_ids": [0]}

    class _FakeModel:
        def eval(self) -> _FakeModel:
            return self

        def to(self, device: str) -> None:
            return None

    def _tokenizer_from_pretrained(model: str, **kwargs: object) -> _FakeTokenizer:
        calls["tokenizer"] = kwargs
        return _FakeTokenizer()

    def _model_from_pretrained(model: str, **kwargs: object) -> _FakeModel:
        calls["model"] = kwargs
        return _FakeModel()

    fake_transformers = types.ModuleType("transformers")
    fake_transformers.AutoTokenizer = types.SimpleNamespace(  # type: ignore[attr-defined]
        from_pretrained=_tokenizer_from_pretrained
    )
    fake_transformers.AutoModelForCausalLM = types.SimpleNamespace(  # type: ignore[attr-defined]
        from_pretrained=_model_from_pretrained
    )

    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: False)  # type: ignore[attr-defined]
    fake_torch.backends = types.SimpleNamespace(  # type: ignore[attr-defined]
        mps=types.SimpleNamespace(is_available=lambda: False)
    )

    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    QwenReranker("Qwen/Qwen3-Reranker-0.6B")

    assert calls["tokenizer"]["local_files_only"] is True
    assert calls["model"]["local_files_only"] is True
