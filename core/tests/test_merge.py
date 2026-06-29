"""Cross-source RRF evidence fusion (ADR-0022 Phase 2c)."""

from __future__ import annotations

from doktok_contracts.schemas import Citation
from doktok_core.agent.merge import evidence_block, merge_evidence


def _c(doc: str, chunk: str, snippet: str = "x") -> Citation:
    return Citation(index=0, document_id=doc, chunk_id=chunk, snippet=snippet)


def test_merge_dedupes_and_reindexes() -> None:
    src_a = [_c("d1", "c1"), _c("d2", "c2")]
    src_b = [_c("d2", "c2"), _c("d3", "c3")]
    merged = merge_evidence([src_a, src_b], limit=8)
    keys = [(c.document_id, c.chunk_id) for c in merged]
    assert keys[0] == ("d2", "c2")  # appears in both sources -> highest fused score, ranked first
    assert len(merged) == 3 and [c.index for c in merged] == [1, 2, 3]


def test_merge_respects_limit() -> None:
    src = [_c(f"d{i}", f"c{i}") for i in range(20)]
    assert len(merge_evidence([src], limit=5)) == 5


def test_merge_relevance_normalized_to_best() -> None:
    merged = merge_evidence([[_c("d1", "c1"), _c("d2", "c2")]], limit=8)
    assert merged[0].relevance == 1.0
    assert merged[1].relevance is not None and merged[1].relevance < 1.0


def test_merge_empty() -> None:
    assert merge_evidence([], limit=8) == []
    assert evidence_block([]) == ""


def test_evidence_block_numbers_and_fences() -> None:
    block = evidence_block([_c("d1", "c1", "the rent is 900")])
    assert "[1] the rent is 900" in block and "treat as data" in block.lower()
