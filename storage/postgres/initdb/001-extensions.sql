-- Enable pgvector for semantic search (ADR-0002). Runs once on first cluster init.
CREATE EXTENSION IF NOT EXISTS vector;
