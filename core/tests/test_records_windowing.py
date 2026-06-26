"""Windowed extraction + seam stitching for long financial documents (#314)."""

from __future__ import annotations

from doktok_contracts.media import ExtractedTransaction
from doktok_contracts.schemas import ExtractedRecord
from doktok_core.aggregation import normalize_transaction
from doktok_core.aggregation.windowing import stitch_windows, window_text


def _records(*rows: tuple[str, str, str]) -> list[ExtractedRecord]:
    """Normalize (date, merchant, amount) tuples into records the way the feature does."""
    out: list[ExtractedRecord] = []
    for date, merchant, amount in rows:
        tx = ExtractedTransaction("", date, merchant, None, amount, "EUR", "debit")
        record = normalize_transaction(tx, tenant_id="t1", document_id="d1")
        assert record is not None
        out.append(record)
    return out


def test_short_text_is_a_single_window() -> None:
    assert window_text("one\ntwo\nthree") == ["one\ntwo\nthree"]


def test_empty_text_yields_no_windows() -> None:
    assert window_text("") == []
    assert window_text("\n\n") != []  # whitespace lines still split; caller guards on .strip()


def test_windows_split_on_line_boundaries_and_overlap() -> None:
    lines = [f"line-{i:03d}\n" for i in range(60)]  # ~9 chars each
    text = "".join(lines)
    windows = window_text(text, window_chars=100, overlap_lines=3)
    assert len(windows) > 1
    # Every window is whole lines (never a mid-line cut) and within the cap (bar an oversized line).
    for w in windows:
        assert w.endswith("\n")
        assert len(w) <= 100
    # Adjacent windows share exactly the overlap tail/head.
    for a, b in zip(windows, windows[1:], strict=False):
        assert a.splitlines()[-3:] == b.splitlines()[:3]
    # Coverage: concatenating the non-overlapping parts reproduces every original line in order.
    seen: list[str] = []
    prev: list[str] = []
    for w in windows:
        cur = w.splitlines()
        overlap = 3 if seen else 0
        assert cur[:overlap] == prev[-overlap:] if overlap else True
        seen.extend(cur[overlap:])
        prev = cur
    assert seen == [line.rstrip("\n") for line in lines]


def test_oversized_single_line_becomes_its_own_window() -> None:
    text = "x" * 500 + "\n" + "y" * 10
    windows = window_text(text, window_chars=100, overlap_lines=2)
    assert windows[0] == "x" * 500 + "\n"


def test_stitch_drops_seam_overlap_but_keeps_distinct_rows() -> None:
    # window A tail == window B head (the overlap re-extracted the same two rows).
    a = _records(("2026-01-01", "Aldi", "10.00"), ("2026-01-02", "Rewe", "20.00"))
    b = _records(("2026-01-02", "Rewe", "20.00"), ("2026-01-03", "Edeka", "30.00"))
    merged = stitch_windows([a, b])
    merchants = [r.merchant_normalized for r in merged]
    assert merchants == ["aldi", "rewe", "edeka"]  # the duplicated 'rewe' row is removed once


def test_stitch_keeps_genuine_repeat_not_at_a_seam() -> None:
    # Two identical transactions inside ONE window are both real and must survive.
    a = _records(
        ("2026-01-01", "Coffee Shop", "3.50"),
        ("2026-01-01", "Coffee Shop", "3.50"),
        ("2026-01-02", "Rewe", "20.00"),
    )
    b = _records(("2026-01-03", "Edeka", "30.00"))
    merged = stitch_windows([a, b])
    coffees = [r for r in merged if r.merchant_normalized == "coffee shop"]
    assert len(coffees) == 2
    assert len(merged) == 4


def test_stitch_single_window_is_unchanged() -> None:
    a = _records(("2026-01-01", "Aldi", "10.00"))
    assert stitch_windows([a]) == a
