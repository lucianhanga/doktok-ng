"""Integration test for chat-thread persistence (test* tenants only, M6.4 #248)."""

from __future__ import annotations

from doktok_contracts.schemas import Citation, RankedChunk, TurnMetrics
from doktok_storage_postgres import Database, PostgresChatThreadRepository

TENANT = "test-a"


def test_ranking_and_metrics_roundtrip_and_thread_totals(db: Database) -> None:
    repo = PostgresChatThreadRepository(db)
    thread = repo.create_thread(TENANT)
    repo.append_message(TENANT, thread.id, "user", "what is the total?")
    repo.append_message(
        TENANT,
        thread.id,
        "assistant",
        "42 [1].",
        ranking=[
            RankedChunk(
                chunk_id="c1",
                document_id="d1",
                retrieval_score=0.9,
                relevance=1.0,
                selected=True,
                cited=True,
            ),
            RankedChunk(chunk_id="c2", document_id="d2", retrieval_score=0.4, selected=False),
        ],
        metrics=TurnMetrics(
            prompt_tokens=100,
            answer_tokens=20,
            reasoning_tokens=5,
            overhead_tokens=10,
            total_ms=1234,
        ),
    )

    messages = repo.get_messages(TENANT, thread.id)
    assistant = messages[1]
    assert len(assistant.ranking) == 2
    assert assistant.ranking[0].selected and assistant.ranking[0].cited
    assert assistant.metrics is not None
    assert assistant.metrics.reasoning_tokens == 5
    assert assistant.metrics.total_tokens == 135  # 100+20+5+10

    listed = repo.list_threads(TENANT)[0]
    assert listed.total_tokens == 135
    assert listed.total_inference_ms == 1234


def test_thread_roundtrip_messages_and_title(db: Database) -> None:
    repo = PostgresChatThreadRepository(db)
    thread = repo.create_thread(TENANT)
    assert thread.title == "" and thread.message_count == 0

    repo.append_message(TENANT, thread.id, "user", "How many leave days do I get?")
    repo.append_message(TENANT, thread.id, "assistant", "28 days [1].")

    messages = repo.get_messages(TENANT, thread.id)
    assert [(m.role, m.content) for m in messages] == [
        ("user", "How many leave days do I get?"),
        ("assistant", "28 days [1]."),
    ]

    listed = repo.list_threads(TENANT)
    assert len(listed) == 1
    assert listed[0].id == thread.id
    assert listed[0].message_count == 2
    # Title is seeded from the first message.
    assert listed[0].title == "How many leave days do I get?"


def test_rename_sets_manual_title_and_blocks_autoseed(db: Database) -> None:
    repo = PostgresChatThreadRepository(db)
    thread = repo.create_thread(TENANT)

    renamed = repo.update_title(TENANT, thread.id, "Leave policy")
    assert renamed is not None
    assert renamed.title == "Leave policy" and renamed.title_source == "manual"

    # A first message must not overwrite the manual title with the auto-seed.
    repo.append_message(TENANT, thread.id, "user", "How many leave days do I get?")
    assert repo.list_threads(TENANT)[0].title == "Leave policy"

    # Renaming an unknown / other-tenant thread returns None (no cross-tenant write).
    assert repo.update_title(TENANT, "nope", "x") is None
    assert repo.update_title("test-b", thread.id, "hijack") is None


def test_assistant_reasoning_and_citations_roundtrip(db: Database) -> None:
    # Reasoning + source citations must persist so a resumed/reloaded thread re-shows them.
    repo = PostgresChatThreadRepository(db)
    thread = repo.create_thread(TENANT)
    repo.append_message(TENANT, thread.id, "user", "where is my id card?")
    citation = Citation(
        index=1,
        document_id="d1",
        chunk_id="c1",
        original_filename="id.pdf",
        snippet="…",
        relevance=1.0,
    )
    repo.append_message(
        TENANT,
        thread.id,
        "assistant",
        "It is here [1].",
        reasoning="I matched the Personalausweis document.",
        citations=[citation],
    )

    messages = repo.get_messages(TENANT, thread.id)
    assistant = messages[1]
    assert assistant.reasoning == "I matched the Personalausweis document."
    assert [c.document_id for c in assistant.citations] == ["d1"]
    assert assistant.citations[0].relevance == 1.0
    # User turn carries no reasoning/citations.
    assert messages[0].reasoning == "" and messages[0].citations == []


def test_threads_are_tenant_scoped_and_deletable(db: Database) -> None:
    repo = PostgresChatThreadRepository(db)
    mine = repo.create_thread(TENANT)
    other = repo.create_thread("test-b")
    repo.append_message(TENANT, mine.id, "user", "hello")

    assert {t.id for t in repo.list_threads(TENANT)} == {mine.id}
    assert repo.thread_exists(TENANT, mine.id) is True
    assert repo.thread_exists(TENANT, other.id) is False  # cross-tenant invisible

    repo.delete_thread(TENANT, mine.id)
    assert repo.thread_exists(TENANT, mine.id) is False
    assert repo.get_messages(TENANT, mine.id) == []  # messages cascade-deleted
