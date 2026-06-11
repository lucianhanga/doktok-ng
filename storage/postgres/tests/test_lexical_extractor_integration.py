"""Integration tests for the Postgres lexical term extractor (no tenant data written)."""

from __future__ import annotations

from doktok_storage_postgres import Database, PostgresLexicalTermExtractor


def test_keyword_config_removes_stopwords_without_stemming(db: Database) -> None:
    # The non-stemming keyword config (migration 0007) keeps real words but drops stopwords.
    extractor = PostgresLexicalTermExtractor(db)
    terms = {
        t.term
        for t in extractor.extract_terms(
            "The governance and finance services are important and the work continues",
            config="doktok_kw_english",
            limit=50,
        )
    }
    assert "the" not in terms and "and" not in terms and "are" not in terms  # stopwords removed
    assert "governance" in terms and "finance" in terms and "services" in terms  # NOT stemmed


def test_simple_config_keeps_stopwords_with_frequency(db: Database) -> None:
    extractor = PostgresLexicalTermExtractor(db)
    terms = {
        t.term: t.frequency for t in extractor.extract_terms("the the the fox", config="simple")
    }
    assert terms.get("the") == 3  # no stopword removal under 'simple'
    assert "fox" in terms


def test_empty_text_returns_no_terms(db: Database) -> None:
    assert PostgresLexicalTermExtractor(db).extract_terms("   ", config="english") == []


def test_markup_digits_and_short_tokens_are_filtered(db: Database) -> None:
    # OCR engines that emit HTML (tables, embedded base64 images) and raw codes used to leak junk
    # terms like td/tr/table/ahr/80939; they must be filtered while real prose words survive.
    extractor = PostgresLexicalTermExtractor(db)
    terms = {
        t.term
        for t in extractor.extract_terms(
            "<table><tr><td>Investment</td></tr></table> 80939 ab http://x aHR0cHM portfolio",
            config="doktok_kw_english",
            limit=50,
        )
    }
    assert "investment" in terms and "portfolio" in terms  # real words kept
    assert "td" not in terms and "tr" not in terms and "table" not in terms  # HTML tags stripped
    assert "80939" not in terms and "ab" not in terms  # digits + sub-3-char dropped
    assert "http" not in terms and "ahr0chm" not in terms  # encoding leftovers dropped
