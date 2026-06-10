from doktok_contracts.schemas import EntityType
from doktok_core.entities.extractor import RegexEntityExtractor


def _by_type(text: str) -> dict[EntityType, list[str]]:
    out: dict[EntityType, list[str]] = {}
    for e in RegexEntityExtractor().extract(text):
        out.setdefault(e.entity_type, []).append(e.normalized_value)
    return out


def test_extracts_common_types() -> None:
    text = (
        "Contact Jane at JANE@Example.COM or visit https://Example.com/Path. "
        "The invoice total is $1,250.00, due 2026-06-10. "
        "Invoice #INV-2026-001 covers Contract No. CT-42."
    )
    found = _by_type(text)
    assert found[EntityType.EMAIL] == ["jane@example.com"]
    assert found[EntityType.URL] == ["https://example.com/path"]
    assert "$1,250.00" in found[EntityType.MONEY]
    assert "2026-06-10" in found[EntityType.DATE]
    assert "INV-2026-001" in found[EntityType.INVOICE_ID]
    assert "CT-42" in found[EntityType.CONTRACT_ID]


def test_no_entities_in_plain_text() -> None:
    assert RegexEntityExtractor().extract("just some ordinary words here") == []
