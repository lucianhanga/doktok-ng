"""Integration tests for the Postgres lexical term extractor (no tenant data written)."""

from __future__ import annotations

from doktok_storage_postgres import Database, PostgresLexicalTermExtractor


def test_english_config_removes_stopwords_and_stems(db: Database) -> None:
    extractor = PostgresLexicalTermExtractor(db)
    terms = extractor.extract_terms(
        "The quick brown fox jumps over the lazy dog and the cat", config="english", limit=50
    )
    words = {t.term for t in terms}
    assert "the" not in words  # stopword removed
    assert "and" not in words  # stopword removed
    assert "fox" in words
    assert any(t.term == "jump" for t in terms)  # stemmed from "jumps"


def test_simple_config_keeps_stopwords_with_frequency(db: Database) -> None:
    extractor = PostgresLexicalTermExtractor(db)
    terms = {
        t.term: t.frequency for t in extractor.extract_terms("the the the fox", config="simple")
    }
    assert terms.get("the") == 3  # no stopword removal under 'simple'
    assert "fox" in terms


def test_empty_text_returns_no_terms(db: Database) -> None:
    assert PostgresLexicalTermExtractor(db).extract_terms("   ", config="english") == []
