import pytest
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


def test_time_question_answers_directly_without_retrieval() -> None:
    # A current-time question is a deterministic capability: answered in code, no retrieval, no
    # model call - and crucially not refused for "no document evidence" (M6.5).
    retriever = FakeRetriever([_hit(1)])
    chat = FakeChat("should not be called")
    answer = DefaultRagAnswerer(retriever, chat).answer_thread("t1", [], "what time is it?", 5)

    assert answer.grounded is True and "It is" in answer.answer
    assert retriever.seen is None  # retrieval skipped
    assert chat.prompt is None  # no model call at all (not even understanding)


def test_time_question_streams_directly() -> None:
    retriever = FakeRetriever([_hit(1)])
    events = list(
        DefaultRagAnswerer(retriever, FakeChat("nope")).answer_thread_stream(
            "t1", [], "what time is it", 5
        )
    )
    assert [e.type for e in events] == ["meta", "token", "sources", "done"]
    assert "It is" in events[1].delta and events[-1].grounded is True
    assert retriever.seen is None


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


class FakeStreamingChatWithUsage:
    """A streaming chat model that also reports token/timing usage (M8 PR3/PR4)."""

    def __init__(self, reply: str, *, reasoning: str = "") -> None:
        self._reply = reply
        self._reasoning = reasoning

    def complete(self, prompt: str) -> str:  # the understanding (rewrite) call
        return prompt and self._reply or self._reply

    def stream_complete(self, prompt, *, think=None):  # type: ignore[no-untyped-def]
        from doktok_contracts.media import ChatChunk

        if self._reasoning:
            yield ChatChunk(kind="reasoning", text=self._reasoning)
        yield ChatChunk(kind="answer", text=self._reply)

    def get_last_usage(self):  # type: ignore[no-untyped-def]
        from doktok_contracts.media import LlmUsage

        return LlmUsage(
            prompt_tokens=100, answer_tokens=20, reasoning_tokens=5, wall_ms=50, estimated=False
        )


def test_stream_emits_ranking_and_metrics() -> None:
    retriever = FakeRetriever([_hit(1), _hit(2), _hit(3)])
    chat = FakeStreamingChatWithUsage("Grounded answer [1].", reasoning="thinking...")
    events = list(DefaultRagAnswerer(retriever, chat).answer_thread_stream("t1", [], "q", 3))
    types = [e.type for e in events]
    assert "ranking" in types and "metrics" in types
    # ranking precedes sources precedes metrics precedes done
    assert types.index("ranking") < types.index("sources") < types.index("metrics")
    assert types[-1] == "done"

    ranking_ev = next(e for e in events if e.type == "ranking")
    assert len(ranking_ev.ranking) == 3
    assert ranking_ev.ranking[0].selected is True
    # [1] was cited in the answer -> the first ranked chunk is flagged cited.
    assert ranking_ev.ranking[0].cited is True

    metrics_ev = next(e for e in events if e.type == "metrics")
    assert metrics_ev.metrics is not None
    assert metrics_ev.metrics.reasoning_tokens == 5
    assert metrics_ev.metrics.answer_tokens == 20
    assert metrics_ev.metrics.total_tokens >= 125


def test_stream_flags_followup_reuse_in_metrics() -> None:
    # The rewrite turns the follow-up into a standalone query -> reused_previous_results=True.
    retriever = FakeRetriever([_hit(1)])
    chat = FakeStreamingChatWithUsage('{"query": "standalone rewritten query", "category": null}')
    history = [type("T", (), {"role": "user", "content": "earlier"})()]
    events = list(
        DefaultRagAnswerer(retriever, chat).answer_thread_stream("t1", history, "and then?", 3)
    )
    metrics_ev = next(e for e in events if e.type == "metrics")
    assert metrics_ev.metrics is not None
    assert metrics_ev.metrics.reused_previous_results is True


def test_stream_surfaces_rewrite_and_rerank_reasoning() -> None:
    from doktok_core.rag.reranker import LlmReranker

    # A streaming chat that thinks on every call (rewrite, rerank, answer).
    chat = FakeStreamingChatWithUsage('{"query": "q"}', reasoning="pondering")
    retriever = FakeRetriever([_hit(1), _hit(2)])
    answerer = DefaultRagAnswerer(retriever, chat, reranker=LlmReranker(chat))
    events = list(answerer.answer_thread_stream("t1", [], "q", 2))
    types = [e.type for e in events]

    # Reasoning is streamed during the understand phase (before "Searching") and during the
    # rerank phase (before "Composing") - not only during the final answer.
    searching = types.index("step", types.index("step") + 1)  # 2nd step = "Searching..."
    composing = max(i for i, t in enumerate(types) if t == "step")  # "Composing the answer"
    reasoning_before_search = (
        any(
            t == "reasoning"
            for t in types[: types.index("step")]  # never, step is first
        )
        or any(e.type == "reasoning" for e in events[1:searching])
    )
    reasoning_before_compose = any(e.type == "reasoning" for e in events[searching:composing])
    assert reasoning_before_search  # understand-phase thinking streamed
    assert reasoning_before_compose  # rerank-phase thinking streamed


# ---------------------------------------------------------------------------
# rerank_score: real score as relevance + rerank threshold tests
# ---------------------------------------------------------------------------


class ScoringFakeReranker:
    """A reranker that sets rerank_score on the returned hits (mimics QwenReranker behaviour)."""

    def __init__(self, scores: list[float]) -> None:
        self._scores = scores  # one score per input hit, positionally

    def rerank(self, query: str, hits: list[SearchHit], *, top_k: int) -> list[SearchHit]:
        order = sorted(range(len(hits)), key=lambda i: self._scores[i], reverse=True)
        return [hits[i].model_copy(update={"rerank_score": self._scores[i]}) for i in order[:top_k]]


def test_relevance_uses_rerank_score_when_present() -> None:
    # When the reranker sets rerank_score, citations carry that real score as relevance (not rank).
    scores = [0.2, 0.8, 0.5]
    retriever = FakeRetriever([_hit(1), _hit(2), _hit(3)])
    chat = FakeChat("Answer with no markers.")
    answerer = DefaultRagAnswerer(
        retriever, chat, reranker=ScoringFakeReranker(scores), retrieve_k=40
    )
    answer = answerer.answer("t1", "q", 3)

    by_chunk = {c.chunk_id: c.relevance for c in answer.citations}
    # Hit 2 gets rerank_score 0.8, hit 3 gets 0.5, hit 1 gets 0.2.
    assert by_chunk["c2"] == pytest.approx(0.8)
    assert by_chunk["c3"] == pytest.approx(0.5)
    assert by_chunk["c1"] == pytest.approx(0.2)
    # Values are NOT the positional (1.0, 0.666, 0.333) - they are the real scores.
    assert by_chunk["c2"] != pytest.approx(1.0)


def test_no_reranker_still_uses_positional_relevance() -> None:
    # Without a reranker, relevance falls back to normalized rank as before (no regression).
    retriever = FakeRetriever([_hit(1), _hit(2), _hit(3)])
    chat = FakeChat("Answer with no markers.")
    answer = DefaultRagAnswerer(retriever, chat).answer("t1", "q", 3)

    by_chunk = {c.chunk_id: c.relevance for c in answer.citations}
    assert by_chunk["c1"] == pytest.approx(1.0)
    assert by_chunk["c2"] == pytest.approx(2 / 3)
    assert by_chunk["c3"] == pytest.approx(1 / 3)


def test_rerank_threshold_drops_low_score_hits() -> None:
    # Hits with rerank_score below the threshold are removed from the context.
    scores = [0.8, 0.1, 0.05]  # only hit 1 clears a 0.3 threshold
    retriever = FakeRetriever([_hit(1), _hit(2), _hit(3)])
    chat = FakeChat("Answer with no markers.")
    answerer = DefaultRagAnswerer(
        retriever,
        chat,
        reranker=ScoringFakeReranker(scores),
        retrieve_k=40,
        rerank_min_relevance=0.3,
    )
    answer = answerer.answer("t1", "q", 3)

    # Only the hit that cleared the threshold is in the citations.
    chunk_ids = {c.chunk_id for c in answer.citations}
    assert "c1" in chunk_ids
    assert "c2" not in chunk_ids
    assert "c3" not in chunk_ids


def test_rerank_threshold_keeps_top_one_when_all_below() -> None:
    # Safety: when every hit is below the threshold, exactly the top-1 must be kept.
    scores = [0.2, 0.1, 0.05]  # all below 0.3
    retriever = FakeRetriever([_hit(1), _hit(2), _hit(3)])
    chat = FakeChat("Answer with no markers.")
    answerer = DefaultRagAnswerer(
        retriever,
        chat,
        reranker=ScoringFakeReranker(scores),
        retrieve_k=40,
        rerank_min_relevance=0.3,
    )
    answer = answerer.answer("t1", "q", 3)

    # The answerer must not refuse - it still grounded with the single best hit.
    assert answer.grounded is True
    assert len(answer.citations) == 1
    # The best hit (c1 with score 0.2, ranked first) is the one kept.
    assert answer.citations[0].chunk_id == "c1"


def test_rerank_threshold_not_applied_when_scores_all_none() -> None:
    # When no reranker ran (all rerank_score=None), the threshold is skipped entirely.
    retriever = FakeRetriever([_hit(1), _hit(2), _hit(3)])
    chat = FakeChat("Answer with no markers.")
    # FakeReranker (from the earlier test) does NOT set rerank_score.
    answerer = DefaultRagAnswerer(
        retriever,
        chat,
        reranker=FakeReranker(),
        retrieve_k=40,
        rerank_min_relevance=0.9,  # impossibly high threshold, must be ignored
    )
    answer = answerer.answer("t1", "q", 3)

    # All 3 hits survive because the threshold was skipped (no rerank_score on any hit).
    assert answer.grounded is True
    assert len(answer.citations) == 3


def test_rerank_threshold_applied_in_streaming_path() -> None:
    # The threshold must also apply in answer_thread_stream (consistent with the one-shot path).
    scores = [0.8, 0.05, 0.05]  # only hit 1 clears 0.3
    retriever = FakeRetriever([_hit(1), _hit(2), _hit(3)])
    chat = FakeStreamingChatWithUsage("Answer with no markers.")
    answerer = DefaultRagAnswerer(
        retriever,
        chat,
        reranker=ScoringFakeReranker(scores),
        retrieve_k=40,
        rerank_min_relevance=0.3,
    )
    events = list(answerer.answer_thread_stream("t1", [], "q", 3))
    sources_ev = next(e for e in events if e.type == "sources")
    chunk_ids = {c.chunk_id for c in sources_ev.citations}
    assert "c1" in chunk_ids
    assert "c2" not in chunk_ids
    assert "c3" not in chunk_ids
