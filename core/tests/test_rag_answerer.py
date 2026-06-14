from doktok_contracts.schemas import QueryFilters, SearchHit
from doktok_core.rag.answerer import REFUSAL, DefaultRagAnswerer


class FakeRetriever:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits
        self.seen: tuple[str, str, int] | None = None
        self.seen_filters: QueryFilters | None = None

    def search(self, tenant_id, query, limit=10, *, filters=None):  # type: ignore[no-untyped-def]
        self.seen = (tenant_id, query, limit)
        self.seen_filters = filters
        return self._hits


class FakeChat:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompt: str | None = None

    def complete(self, prompt: str) -> str:
        self.prompt = prompt
        return self._reply


def _hit(i: int) -> SearchHit:
    return SearchHit(
        document_id=f"d{i}",
        chunk_id=f"c{i}",
        original_filename=f"f{i}.txt",
        page_start=1,
        snippet="snippet text",
        text="full chunk text body",
        score=1.0,
    )


def test_grounded_answer_with_citations() -> None:
    retriever = FakeRetriever([_hit(1), _hit(2)])
    chat = FakeChat("The answer is X [1].")
    answer = DefaultRagAnswerer(retriever, chat).answer("t1", "what is X?", 5)

    assert answer.grounded is True
    assert answer.answer == "The answer is X [1]."
    # Citation guardrail: only the excerpt the answer actually referenced ([1]) is cited.
    assert [c.index for c in answer.citations] == [1]
    assert answer.citations[0].document_id == "d1"
    assert retriever.seen == ("t1", "what is X?", 5)
    assert chat.prompt is not None and "full chunk text body" in chat.prompt


def test_refuses_when_no_hits() -> None:
    answer = DefaultRagAnswerer(FakeRetriever([]), FakeChat("anything")).answer("t1", "q")
    assert answer.grounded is False
    assert answer.answer == REFUSAL
    assert answer.citations == []


def test_refuses_when_model_refuses() -> None:
    answer = DefaultRagAnswerer(FakeRetriever([_hit(1)]), FakeChat(REFUSAL)).answer("t1", "q")
    assert answer.grounded is False
    assert answer.citations == []


def test_refuses_on_model_error() -> None:
    class Boom:
        def complete(self, prompt: str) -> str:
            raise RuntimeError("model down")

    answer = DefaultRagAnswerer(FakeRetriever([_hit(1)]), Boom()).answer("t1", "q")
    assert answer.grounded is False
    assert answer.answer == REFUSAL


def test_empty_question_refuses_without_calling_model() -> None:
    chat = FakeChat("should not be called")
    answer = DefaultRagAnswerer(FakeRetriever([_hit(1)]), chat).answer("t1", "   ")
    assert answer.grounded is False
    assert chat.prompt is None


class FakeReranker:
    def __init__(self) -> None:
        self.seen: tuple[str, int, int] | None = None

    def rerank(self, query: str, hits: list[SearchHit], *, top_k: int):  # type: ignore[no-untyped-def]
        self.seen = (query, len(hits), top_k)
        return list(reversed(hits))[:top_k]  # reverse to prove the reranker order is used


def test_reranker_retrieves_wide_then_keeps_top_k() -> None:
    retriever = FakeRetriever([_hit(i) for i in range(1, 7)])  # 6 candidates
    reranker = FakeReranker()
    chat = FakeChat("Answer using [1].")
    answerer = DefaultRagAnswerer(retriever, chat, reranker=reranker, retrieve_k=40)
    answer = answerer.answer("t1", "q", 3)

    assert retriever.seen == ("t1", "q", 40)  # retrieved wide
    assert reranker.seen == ("q", 6, 3)  # reranked the 6 candidates to top 3
    assert answer.grounded is True
    # only one excerpt cited (the answer referenced [1])
    assert [c.index for c in answer.citations] == [1]


def _hit_score(i: int, score: float, text: str = "full chunk text body") -> SearchHit:
    return SearchHit(
        document_id=f"d{i}",
        chunk_id=f"c{i}",
        original_filename=f"f{i}.txt",
        page_start=1,
        snippet="snippet text",
        text=text,
        score=score,
    )


def test_refuses_below_min_score_without_calling_model() -> None:
    retriever = FakeRetriever([_hit_score(1, 0.005)])
    chat = FakeChat("should not be called")
    answer = DefaultRagAnswerer(retriever, chat, min_score=0.02).answer("t1", "q")
    assert answer.grounded is False and answer.answer == REFUSAL
    assert chat.prompt is None  # model never invoked


def test_answers_when_score_clears_floor() -> None:
    retriever = FakeRetriever([_hit_score(1, 0.5)])
    answer = DefaultRagAnswerer(retriever, FakeChat("X [1]."), min_score=0.02).answer("t1", "q")
    assert answer.grounded is True


def test_document_bracket_markers_are_neutralized_in_context() -> None:
    # A document containing "[1]" must not be able to forge a citation marker in the prompt.
    retriever = FakeRetriever([_hit_score(1, 1.0, text="see clause [1] and [99] below")])
    chat = FakeChat("answer [1].")
    DefaultRagAnswerer(retriever, chat).answer("t1", "q")
    assert chat.prompt is not None
    assert "clause (1) and (99)" in chat.prompt  # document brackets neutralized to parens
    assert "[99]" not in chat.prompt  # the forged high marker is gone


class ScriptedChat:
    """Returns JSON for the understanding prompt, the answer otherwise (multi-turn, ADR-0018 P2)."""

    def __init__(self, understanding: str, answer: str) -> None:
        self._understanding = understanding
        self._answer = answer
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        # The understanding call asks for a JSON object (ends with "JSON:"); else it answers.
        return self._understanding if "JSON:" in prompt else self._answer


def test_answer_thread_rewrites_followup_against_history() -> None:
    from doktok_contracts.schemas import ChatTurn

    retriever = FakeRetriever([_hit(1)])
    chat = ScriptedChat(
        understanding='{"query": "Block House spend in March 2026", "category": null, '
        '"date_from": null, "date_to": null}',
        answer="EUR 120 [1].",
    )
    history = [
        ChatTurn(role="user", content="how much did I spend at Block House?"),
        ChatTurn(role="assistant", content="EUR 500 total [1]."),
    ]

    answer = DefaultRagAnswerer(retriever, chat).answer_thread(
        "t1", history, "what about March?", 5
    )

    # Retrieval used the REWRITTEN standalone query, not the vague follow-up.
    assert retriever.seen == ("t1", "Block House spend in March 2026", 5)
    assert answer.rewritten_query == "Block House spend in March 2026"
    assert answer.grounded is True


def test_answer_thread_infers_category_and_date_filters() -> None:
    from doktok_contracts.schemas import ChatTurn

    retriever = FakeRetriever([_hit(1)])
    chat = ScriptedChat(
        understanding='{"query": "late fees", "category": "invoice", '
        '"date_from": "2023-01-01", "date_to": "2023-12-31"}',
        answer="Late fees are 2% [1].",
    )
    history = [ChatTurn(role="user", content="anything about my invoices?")]
    answer = DefaultRagAnswerer(retriever, chat).answer_thread(
        "t1", history, "what about late fees in 2023?", 5
    )

    # The inferred filters are passed to the retriever and surfaced on the answer.
    assert retriever.seen_filters is not None
    assert retriever.seen_filters.category == "invoice"
    assert str(retriever.seen_filters.date_from) == "2023-01-01"
    assert answer.filters is not None and answer.filters.category == "invoice"


def test_answer_thread_without_history_still_answers() -> None:
    retriever = FakeRetriever([_hit(1)])
    # The understanding call returns no usable JSON -> falls back to the original question.
    chat = ScriptedChat(understanding="not json", answer="The answer is X [1].")

    answer = DefaultRagAnswerer(retriever, chat).answer_thread("t1", [], "what is X?", 5)

    assert retriever.seen == ("t1", "what is X?", 5)  # fell back to the original question
    assert answer.rewritten_query is None
    assert answer.filters is None


def test_answer_thread_rewrite_failure_falls_back_to_question() -> None:
    from doktok_contracts.schemas import ChatTurn

    class RewriteBoom:
        def complete(self, prompt: str) -> str:
            if "Standalone query:" in prompt:
                raise RuntimeError("rewrite model down")
            return "Answer from original [1]."

    retriever = FakeRetriever([_hit(1)])
    history = [ChatTurn(role="user", content="earlier question")]
    answer = DefaultRagAnswerer(retriever, RewriteBoom()).answer_thread(
        "t1", history, "the follow-up", 5
    )
    # Rewrite failed -> retrieval falls back to the original follow-up; still answers.
    assert retriever.seen == ("t1", "the follow-up", 5)
    assert answer.grounded is True


def test_citations_carry_normalized_relevance() -> None:
    # No reranker: relevance follows retrieval order, normalized so the top hit = 1.0 (M6.4).
    # Keyed by chunk_id (it reflects the relevance order, surviving the edges-best repack).
    retriever = FakeRetriever([_hit(1), _hit(2), _hit(3), _hit(4)])
    chat = FakeChat("Answer with no markers.")  # no [n] -> all hits returned as sources
    answer = DefaultRagAnswerer(retriever, chat).answer("t1", "q", 4)

    by_chunk = {c.chunk_id: c.relevance for c in answer.citations}
    assert by_chunk["c1"] == 1.0  # first retrieved -> most relevant
    assert by_chunk["c4"] == 0.25  # last of 4 -> (4-3)/4
    values = sorted((v for v in by_chunk.values() if v is not None), reverse=True)
    assert values == [1.0, 0.75, 0.5, 0.25]
