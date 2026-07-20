"""Per-tenant client resolution in the worker (epic #708, T2): the resolver caches per tenant and
features resolve their extractor for the document's tenant at process time."""

from __future__ import annotations

from doktok_worker.composition import TenantClientResolver


class _Fake:
    def __init__(self, tag: str) -> None:
        self.tag = tag


def test_resolver_builds_once_per_tenant_and_caches() -> None:
    builds: list[str] = []

    def build(tid: str) -> _Fake:
        builds.append(tid)
        return _Fake(tid)

    resolver = TenantClientResolver(build)
    a1 = resolver.clients_for("t-a")
    a2 = resolver.clients_for("t-a")
    b = resolver.clients_for("t-b")
    assert a1 is a2  # cached per tenant
    assert builds == ["t-a", "t-b"]
    assert a1.tag == "t-a" and b.tag == "t-b"


def test_resolver_clear_forces_rebuild() -> None:
    builds: list[str] = []

    def build(tid: str) -> _Fake:
        builds.append(tid)
        return _Fake(tid)

    resolver = TenantClientResolver(build)
    first = resolver.clients_for("t-a")
    resolver.clear()
    second = resolver.clients_for("t-a")
    assert first is not second
    assert builds == ["t-a", "t-a"]


# --- a feature resolves per tenant at process time (wiring proof with DocMetadataFeature) ---

from datetime import UTC, datetime  # noqa: E402

from doktok_contracts.media import ExtractedMetadata  # noqa: E402
from doktok_contracts.schemas import Document, DocumentStatus  # noqa: E402
from doktok_core.documents.inmemory import InMemoryDocumentRepository  # noqa: E402
from doktok_core.features.processors import DocMetadataFeature  # noqa: E402


class _FakeFiles:
    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    def read_bytes(self, path: str) -> bytes:
        try:
            return self._files[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    def move(self, source: str, destination: str) -> None: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    def write_text(self, path: str, text: str) -> None: ...


class _FakeExtractor:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def extract(self, text: str) -> ExtractedMetadata:
        return ExtractedMetadata(title=self.tag, document_date="", location="", summary="")


def _doc(doc_id: str, tenant: str) -> Document:
    return Document(
        id=doc_id,
        tenant_id=tenant,
        sha256="x",
        original_filename="report.pdf",
        title="report",
        status=DocumentStatus.ACTIVE,
        storage_path=f"/store/{doc_id}",
        created_at=datetime.now(UTC),
    )


def test_feature_uses_the_documents_tenant_stack() -> None:
    seen: list[str] = []
    fakes = {tid: _FakeExtractor(f"title-from-{tid}") for tid in ("t1", "t2")}
    repo = InMemoryDocumentRepository()
    repo.add(_doc("d1", "t1"))
    repo.add(_doc("d2", "t2"))
    files = _FakeFiles(
        {
            "/store/d1/content.md": b"some real content here for metadata",
            "/store/d2/content.md": b"other tenant content for metadata",
        }
    )

    def resolve(tid: str) -> _FakeExtractor:
        seen.append(tid)
        return fakes[tid]

    feature = DocMetadataFeature(repo, files, resolve)
    feature.process("t1", "d1")
    feature.process("t2", "d2")
    assert seen == ["t1", "t2"]
    assert repo.get("t1", "d1").title == "title-from-t1"  # type: ignore[union-attr]
    assert repo.get("t2", "d2").title == "title-from-t2"  # type: ignore[union-attr]
