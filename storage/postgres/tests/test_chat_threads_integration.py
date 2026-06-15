"""Integration test for chat-thread persistence (test* tenants only, M6.4 #248)."""

from __future__ import annotations

from doktok_contracts.schemas import Citation
from doktok_storage_postgres import Database, PostgresChatThreadRepository

TENANT = "test-a"


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
