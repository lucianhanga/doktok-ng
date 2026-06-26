"""Hybrid retrieval (pgvector + Postgres full-text search)."""

from doktok_retrieval_hybrid.retriever import HybridPostgresRetriever

__version__ = "0.2.0"

__all__ = ["HybridPostgresRetriever"]
