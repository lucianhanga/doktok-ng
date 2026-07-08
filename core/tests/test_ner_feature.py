"""NerFeature: LLM-assisted PERSON/ORG/GPE extraction into the shared entity store (M7.4).

Critically, it owns ONLY the NER entity types: re-running it must not touch rule-based entities or
keywords (and vice-versa), so the two features coexist in document_entities.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from doktok_contracts.media import ExtractedEntity
from doktok_contracts.schemas import Document, DocumentEntity, DocumentStatus, EntityType
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.entities.inmemory import InMemoryEntityRepository
from doktok_core.features.processors import NerFeature


class FakeFileStorage:
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


class FakeNer:
    def __init__(self, entities: list[ExtractedEntity]) -> None:
        self._entities = entities
        self.seen: str | None = None

    def extract(self, text: str) -> list[ExtractedEntity]:
        self.seen = text
        return self._entities


def _occ(text: str, t: EntityType) -> ExtractedEntity:
    return ExtractedEntity(
        entity_text=text, entity_type=t, normalized_value=text, start_offset=0, end_offset=0
    )


def _doc() -> Document:
    return Document(
        id="d1",
        tenant_id="t1",
        sha256="x",
        original_filename="letter.pdf",
        title="letter",
        status=DocumentStatus.ACTIVE,
        storage_path="/store/d1",
        created_at=datetime.now(UTC),
    )


def _entity(doc_id: str, text: str, t: EntityType) -> DocumentEntity:
    return DocumentEntity(
        id=uuid.uuid4().hex,
        tenant_id="t1",
        document_id=doc_id,
        version_id="",
        entity_text=text,
        entity_type=t,
        normalized_value=text.casefold(),
        frequency=1,
    )


def test_stores_named_entities_with_frequency() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(_doc())
    files = FakeFileStorage(
        {"/store/d1/content.md": b"Angela Merkel met Siemens. Angela Merkel spoke in Berlin."}
    )
    ner = FakeNer(
        [
            _occ("Angela Merkel", EntityType.PERSON),
            _occ("Siemens", EntityType.ORG),
            _occ("Berlin", EntityType.GPE),
        ]
    )
    shared = InMemoryEntityRepository()
    NerFeature(repo, files, ner, shared).process("t1", "d1")

    stored = {(e.entity_type, e.normalized_value): e for e in shared.list_for_document("t1", "d1")}
    assert (EntityType.PERSON, "angela merkel") in stored
    assert (EntityType.ORG, "siemens") in stored
    assert (EntityType.GPE, "berlin") in stored
    # frequency counts mentions of the name in the text
    assert stored[(EntityType.PERSON, "angela merkel")].frequency == 2
    assert ner.seen is not None and "Siemens" in ner.seen


def test_replaces_only_ner_types_leaving_rule_based_entities() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(_doc())
    files = FakeFileStorage({"/store/d1/content.md": b"Bob works at IBM."})
    shared = InMemoryEntityRepository()
    # pre-existing rule-based entities + a stale NER row that should be replaced
    shared.add_entities(
        [
            _entity("d1", "invoice", EntityType.CUSTOM_TOKEN),
            _entity("d1", "a@b.com", EntityType.EMAIL),
            _entity("d1", "Old Person", EntityType.PERSON),
        ]
    )
    ner = FakeNer([_occ("Bob", EntityType.PERSON), _occ("IBM", EntityType.ORG)])
    NerFeature(repo, files, ner, shared).process("t1", "d1")

    by_type = {e.entity_type for e in shared.list_for_document("t1", "d1")}
    values = {(e.entity_type, e.normalized_value) for e in shared.list_for_document("t1", "d1")}
    # rule-based entities untouched
    assert (EntityType.CUSTOM_TOKEN, "invoice") in values
    assert (EntityType.EMAIL, "a@b.com") in values
    # stale NER replaced with the new extraction
    assert (EntityType.PERSON, "old person") not in values
    assert (EntityType.PERSON, "bob") in values
    assert (EntityType.ORG, "ibm") in values
    assert EntityType.GPE not in by_type  # none in this doc


def test_job_titles_flow_into_document_entities_and_are_ner_owned() -> None:
    """JOB_TITLE (#518 Phase 2) is a NER-owned type: extracted occurrences (multilingual) land in
    document_entities normalized like PERSON/ORG/GPE, and a re-run replaces stale JOB_TITLE rows
    while leaving rule-based entities untouched."""
    repo = InMemoryDocumentRepository()
    repo.add(_doc())
    files = FakeFileStorage(
        {
            "/store/d1/content.md": (
                b"Maria Weber ist  Gesch\xc3\xa4ftsf\xc3\xbchrerin. "
                b"A Senior Software Engineer signed."
            )
        }
    )
    shared = InMemoryEntityRepository()
    # a stale JOB_TITLE row (must be replaced) + a rule-based row (must survive)
    shared.add_entities(
        [
            _entity("d1", "Old Title", EntityType.JOB_TITLE),
            _entity("d1", "a@b.com", EntityType.EMAIL),
        ]
    )
    ner = FakeNer(
        [
            _occ("Maria Weber", EntityType.PERSON),
            _occ("Geschäftsführerin", EntityType.JOB_TITLE),
            _occ("Senior  Software Engineer", EntityType.JOB_TITLE),  # ragged whitespace
        ]
    )
    NerFeature(repo, files, ner, shared).process("t1", "d1")

    values = {(e.entity_type, e.normalized_value) for e in shared.list_for_document("t1", "d1")}
    # multilingual titles stored, normalized via normalize_ner_name (casefold + collapse spaces)
    assert (EntityType.JOB_TITLE, "geschäftsführerin") in values
    assert (EntityType.JOB_TITLE, "senior software engineer") in values
    assert (EntityType.PERSON, "maria weber") in values
    # NER re-run replaces stale JOB_TITLE rows but never rule-based rows
    assert (EntityType.JOB_TITLE, "old title") not in values
    assert (EntityType.EMAIL, "a@b.com") in values


def test_skips_when_no_content_but_clears_stale_ner() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(_doc())
    shared = InMemoryEntityRepository()
    shared.add_entities([_entity("d1", "Ghost", EntityType.PERSON)])
    ner = FakeNer([_occ("ShouldNotBeUsed", EntityType.PERSON)])
    NerFeature(repo, FakeFileStorage({}), ner, shared).process("t1", "d1")

    assert ner.seen is None  # model never called on empty content
    assert shared.list_for_document("t1", "d1") == []  # stale NER cleared
