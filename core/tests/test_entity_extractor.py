from doktok_contracts.schemas import EntityType
from doktok_core.entities.extractor import RegexEntityExtractor


def _by_type(text: str) -> dict[EntityType, list[str]]:
    out: dict[EntityType, list[str]] = {}
    for e in RegexEntityExtractor().extract(text):
        out.setdefault(e.entity_type, []).append(e.normalized_value)
    return out


def test_extracts_email_and_url_only() -> None:
    # M8.x (#312): only EMAIL + URL are extracted by regex now; MONEY/DATE/INVOICE_ID/CONTRACT_ID
    # were dropped as low-value noise.
    text = (
        "Contact Jane at JANE@Example.COM or visit https://Example.com/Path. "
        "The invoice total is $1,250.00, due 2026-06-10. "
        "Invoice #INV-2026-001 covers Contract No. CT-42."
    )
    found = _by_type(text)
    assert found[EntityType.EMAIL] == ["jane@example.com"]
    assert found[EntityType.URL] == ["https://example.com/path"]
    assert set(found) == {EntityType.EMAIL, EntityType.URL}  # nothing else extracted


def test_no_entities_in_plain_text() -> None:
    assert RegexEntityExtractor().extract("just some ordinary words here") == []
