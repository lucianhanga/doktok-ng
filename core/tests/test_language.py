from doktok_core.entities.language import SIMPLE_CONFIG, detect_language, pg_config_for


def test_detects_english() -> None:
    assert (
        detect_language("This is clearly an English sentence about invoices and documents.") == "en"
    )


def test_detects_french() -> None:
    text = "Ceci est une phrase clairement en francais avec des factures et des documents."
    assert detect_language(text) == "fr"


def test_short_text_is_unknown() -> None:
    assert detect_language("hi") == "unknown"


def test_pg_config_mapping() -> None:
    assert pg_config_for("en") == "english"
    assert pg_config_for("de") == "german"
    assert pg_config_for("fr") == "french"
    assert pg_config_for("xx") == SIMPLE_CONFIG
    assert pg_config_for("unknown") == SIMPLE_CONFIG
