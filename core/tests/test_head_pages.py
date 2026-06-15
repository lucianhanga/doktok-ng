"""Unit tests for the first-pages enrichment budget helper (#311)."""

from __future__ import annotations

import json

from doktok_core.features.processors import _head_pages


class FakeStorage:
    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    def read_bytes(self, path: str) -> bytes:
        name = path.rsplit("/", 1)[-1]
        if name not in self._files:
            raise FileNotFoundError(path)
        return self._files[name]


def _json_pages(*pages: str) -> bytes:
    return json.dumps({"pages": [{"text": p} for p in pages]}).encode()


def test_takes_first_n_pages() -> None:
    storage = FakeStorage({"content.json": _json_pages("page one", "page two", "page three")})
    assert _head_pages(storage, "/x", max_pages=2, max_chars=1000) == "page one\n\npage two"


def test_caps_at_max_chars() -> None:
    storage = FakeStorage({"content.json": _json_pages("a" * 100, "b" * 100)})
    assert _head_pages(storage, "/x", max_pages=2, max_chars=50) == "a" * 50


def test_short_doc_returned_whole() -> None:
    storage = FakeStorage({"content.json": _json_pages("tiny")})
    assert _head_pages(storage, "/x", max_pages=3, max_chars=1000) == "tiny"


def test_falls_back_to_content_md_without_pages() -> None:
    storage = FakeStorage({"content.md": b"plain markdown body"})
    assert _head_pages(storage, "/x", max_pages=2, max_chars=1000) == "plain markdown body"


def test_malformed_json_falls_back_to_content_md() -> None:
    storage = FakeStorage({"content.json": b"not json", "content.md": b"the real text"})
    assert _head_pages(storage, "/x", max_pages=2, max_chars=1000) == "the real text"
