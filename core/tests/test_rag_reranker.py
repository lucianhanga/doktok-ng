from doktok_contracts.schemas import SearchHit
from doktok_core.rag.reranker import LlmReranker


class FakeChat:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompt: str | None = None

    def complete(self, prompt: str) -> str:
        self.prompt = prompt
        return self._reply


def _hits(n: int) -> list[SearchHit]:
    return [
        SearchHit(
            document_id=f"d{i}",
            chunk_id=f"c{i}",
            snippet=f"passage {i}",
            text=f"passage {i}",
            score=1.0,
        )
        for i in range(n)
    ]


def test_reorders_by_llm_ranking_and_truncates() -> None:
    chat = FakeChat("Here you go: [2, 0, 1]")
    out = LlmReranker(chat).rerank("q", _hits(3), top_k=2)
    assert [h.document_id for h in out] == ["d2", "d0"]
    assert chat.prompt is not None and "passage 2" in chat.prompt


def test_falls_back_to_retrieval_order_on_unparseable_response() -> None:
    out = LlmReranker(FakeChat("no json here")).rerank("q", _hits(3), top_k=2)
    assert [h.document_id for h in out] == ["d0", "d1"]


def test_ignores_out_of_range_and_duplicate_indices() -> None:
    out = LlmReranker(FakeChat("[5, 1, 1, 0]")).rerank("q", _hits(3), top_k=3)
    assert [h.document_id for h in out] == ["d1", "d0"]  # 5 dropped, dup 1 dropped


def test_single_hit_passthrough_without_calling_model() -> None:
    chat = FakeChat("should not be called")
    out = LlmReranker(chat).rerank("q", _hits(1), top_k=3)
    assert len(out) == 1 and chat.prompt is None
