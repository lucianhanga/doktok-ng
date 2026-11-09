"""Large-document chunk embedding must be bounded and failure-safe."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Event, Thread, current_thread, local

import pytest
from doktok_contracts.media import LlmUsage
from doktok_contracts.schemas import Document, DocumentChunk, DocumentStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.features.processors import ChunkEmbedFeature
from doktok_core.indexing.chunker import FixedWindowChunker
from doktok_core.indexing.inmemory import InMemoryChunkRepository

TENANT = "t1"
DOC_ID = "large-doc"


class FakeFileStorage:
    def __init__(self, pages_by_document: dict[str, list[str]]) -> None:
        self._content = {
            doc_id: json.dumps({"pages": [{"text": text} for text in pages]}).encode()
            for doc_id, pages in pages_by_document.items()
        }

    def read_bytes(self, path: str) -> bytes:
        return self._content[path.split("/")[-2]]

    def write_bytes(self, path: str, data: bytes) -> None:
        raise AssertionError("writes are not expected in this test")

    def write_text(self, path: str, text: str) -> None:
        raise AssertionError("writes are not expected in this test")

    def move(self, source: str, destination: str) -> None:
        raise AssertionError("moves are not expected in this test")


class FakeEmbedder:
    def __init__(
        self, *, fail_on_call: int | None = None, call_barrier: Barrier | None = None
    ) -> None:
        self.calls: list[list[str]] = []
        self._fail_on_call = fail_on_call
        self._call_barrier = call_barrier
        self._usage_local = local()

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._usage_local.value = None
        if self._fail_on_call == len(self.calls) + 1:
            raise TimeoutError("embedding timed out")
        self.calls.append(texts)
        self._usage_local.value = LlmUsage(
            prompt_tokens=sum(max(1, len(text) // 4) for text in texts),
            wall_ms=100,
            eval_ms=100,
        )
        if self._call_barrier is not None:
            self._call_barrier.wait(timeout=2)
        return [[float(len(text))] for text in texts]

    def get_last_usage(self) -> LlmUsage | None:
        return getattr(self._usage_local, "value", None)


class RaisingUsageEmbedder(FakeEmbedder):
    def get_last_usage(self) -> LlmUsage | None:
        raise RuntimeError("telemetry unavailable")


class TrackingChunkRepository(InMemoryChunkRepository):
    def __init__(self) -> None:
        super().__init__()
        self.replace_calls = 0

    def replace_for_document(
        self,
        tenant_id: str,
        document_id: str,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ) -> None:
        self.replace_calls += 1
        super().replace_for_document(tenant_id, document_id, chunks, embeddings)


class PausingChunkRepository(InMemoryChunkRepository):
    def __init__(self) -> None:
        self.paused = Event()
        self.release = Event()
        super().__init__()

    @property
    def chunks(self) -> list[DocumentChunk]:
        return self._chunks

    @chunks.setter
    def chunks(self, value: list[DocumentChunk]) -> None:
        if current_thread().name == "A":
            self.paused.set()
            assert self.release.wait(2), "timed out waiting to resume A"
        self._chunks = value


def _document(doc_id: str = DOC_ID) -> Document:
    return Document(
        id=doc_id,
        tenant_id=TENANT,
        sha256=(doc_id + "a" * 64)[:64],
        original_filename=f"{doc_id}.pdf",
        status=DocumentStatus.ACTIVE,
        storage_path=f"/docs.active/{doc_id}",
        created_at=datetime.now(UTC),
    )


def _old_chunk(doc_id: str = DOC_ID) -> DocumentChunk:
    return DocumentChunk(
        id="old-chunk",
        tenant_id=TENANT,
        document_id=doc_id,
        version_id="",
        page_start=1,
        page_end=1,
        heading_path=[],
        text="previous index",
        token_count=3,
    )


def _feature(
    embedder: FakeEmbedder, repo: TrackingChunkRepository, pages: list[str]
) -> ChunkEmbedFeature:
    documents = InMemoryDocumentRepository()
    documents.add(_document())
    return ChunkEmbedFeature(
        documents,
        FakeFileStorage({DOC_ID: pages}),
        FixedWindowChunker(),
        embedder,
        repo,
    )


def test_large_document_uses_bounded_batches_and_replaces_index_once() -> None:
    embedder = FakeEmbedder()
    repo = TrackingChunkRepository()
    repo.add_chunks([_old_chunk()], [[99.0]])

    # Five 3000-character pages produce 15 chunks: 1200 + 1200 + 1000 chars per page.
    feature = _feature(embedder, repo, ["x" * 3000] * 5)
    feature.process(TENANT, DOC_ID)

    assert [len(call) for call in embedder.calls] == [8, 7]
    assert repo.replace_calls == 1
    assert len(repo.chunks) == 15
    assert all(chunk.id != "old-chunk" for chunk in repo.chunks)
    assert repo.embeddings[0] == [1200.0]
    assert repo.embeddings[-1] == [1000.0]
    usage = feature.get_last_usage()
    assert usage is not None
    assert usage.prompt_tokens == 4250
    assert usage.wall_ms == 200


def test_batch_failure_preserves_previous_index() -> None:
    embedder = FakeEmbedder(fail_on_call=2)
    repo = TrackingChunkRepository()
    repo.add_chunks([_old_chunk()], [[99.0]])

    with pytest.raises(TimeoutError, match="embedding timed out"):
        _feature(embedder, repo, ["x" * 3000] * 5).process(TENANT, DOC_ID)

    assert repo.replace_calls == 0
    assert [chunk.id for chunk in repo.chunks] == ["old-chunk"]
    assert repo.embeddings == [[99.0]]


def test_in_memory_replace_validates_before_mutating() -> None:
    repo = InMemoryChunkRepository()
    repo.add_chunks([_old_chunk()], [[99.0]])

    with pytest.raises(ValueError):
        repo.replace_for_document(TENANT, DOC_ID, [_old_chunk()], [])

    assert [chunk.id for chunk in repo.chunks] == ["old-chunk"]
    assert repo.embeddings == [[99.0]]


def test_usage_getter_failure_does_not_abort_successful_embedding() -> None:
    repo = TrackingChunkRepository()
    feature = _feature(RaisingUsageEmbedder(), repo, ["x" * 100])

    feature.process(TENANT, DOC_ID)

    assert repo.replace_calls == 1
    assert len(repo.chunks) == 1
    assert feature.get_last_usage() is None


def test_empty_document_clears_index_without_calling_embedder() -> None:
    embedder = FakeEmbedder()
    repo = TrackingChunkRepository()
    repo.add_chunks([_old_chunk()], [[99.0]])

    feature = _feature(embedder, repo, [])
    feature.process(TENANT, DOC_ID)

    assert embedder.calls == []
    assert repo.chunks == []
    assert repo.embeddings == []
    assert feature.get_last_usage() is None


def test_usage_isolated_between_concurrent_documents() -> None:
    call_barrier = Barrier(2)
    processed_barrier = Barrier(2)
    embedder = FakeEmbedder(call_barrier=call_barrier)
    repo = TrackingChunkRepository()
    documents = InMemoryDocumentRepository()
    documents.add(_document("doc-a"))
    documents.add(_document("doc-b"))
    feature = ChunkEmbedFeature(
        documents,
        FakeFileStorage({"doc-a": ["x" * 100], "doc-b": ["y" * 200]}),
        FixedWindowChunker(),
        embedder,
        repo,
    )

    def process(doc_id: str) -> int | None:
        feature.process(TENANT, doc_id)
        processed_barrier.wait(timeout=2)
        usage = feature.get_last_usage()
        return usage.prompt_tokens if usage is not None else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(process, ["doc-a", "doc-b"]))

    assert results == [25, 50]
    assert repo.chunk_counts_for_documents(TENANT, ["doc-a", "doc-b"]) == {
        "doc-a": 1,
        "doc-b": 1,
    }


def test_concurrent_replacements_do_not_lose_either_documents_index() -> None:
    repo = PausingChunkRepository()
    done_b = Event()

    def replace(doc_id: str, done: Event | None = None) -> None:
        repo.replace_for_document(TENANT, doc_id, [_old_chunk(doc_id)], [[2.0]])
        if done is not None:
            done.set()

    thread_a = Thread(name="A", target=replace, args=("doc-a",))
    thread_b = Thread(name="B", target=replace, args=("doc-b", done_b))
    thread_a.start()
    assert repo.paused.wait(1), "A did not reach the assignment"
    thread_b.start()
    try:
        # Without a lock B finishes and A overwrites it; with a lock B waits for A.
        done_b.wait(0.1)
    finally:
        repo.release.set()
    thread_a.join(1)
    thread_b.join(1)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert repo.chunk_counts_for_documents(TENANT, ["doc-a", "doc-b"]) == {
        "doc-a": 1,
        "doc-b": 1,
    }
