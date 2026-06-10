from doktok_core.indexing.chunker import FixedWindowChunker


def test_short_text_is_one_chunk() -> None:
    chunks = FixedWindowChunker(max_chars=100, overlap=20).chunk("hello world")
    assert len(chunks) == 1
    assert chunks[0].text == "hello world"
    assert chunks[0].start_offset == 0
    assert chunks[0].end_offset == 11


def test_windows_overlap_and_cover_text() -> None:
    text = "abcdefghij" * 10  # 100 chars
    chunks = FixedWindowChunker(max_chars=40, overlap=10).chunk(text)
    # step = 30 -> windows start at 0, 30, 60 (the third reaches the end at 100)
    assert [c.start_offset for c in chunks] == [0, 30, 60]
    assert chunks[0].end_offset == 40
    assert chunks[-1].end_offset == 100


def test_deterministic() -> None:
    text = "lorem ipsum dolor sit amet " * 20
    a = FixedWindowChunker(max_chars=80, overlap=16).chunk(text)
    b = FixedWindowChunker(max_chars=80, overlap=16).chunk(text)
    assert [(c.start_offset, c.end_offset, c.text) for c in a] == [
        (c.start_offset, c.end_offset, c.text) for c in b
    ]


def test_blank_text_yields_no_chunks() -> None:
    assert FixedWindowChunker().chunk("   \n  ") == []
