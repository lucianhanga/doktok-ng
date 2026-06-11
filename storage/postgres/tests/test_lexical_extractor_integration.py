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
