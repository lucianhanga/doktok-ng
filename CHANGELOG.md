# Changelog

All notable changes to DokTok NG are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Project kickoff: architecture proposal, six ADRs, and the M0-M10 milestone roadmap.
- Repository metadata, issue templates, and the tracked backlog (granular M0 tickets + M1-M10 epics).
- M0 skeleton: uv + pnpm monorepo with 12 workspace packages; contracts-first ports and schemas;
  core settings (`DOKTOK_*`) and DI registry skeleton; FastAPI backend with `GET /health`;
  React + Vite UI shell with a backend status panel; PostgreSQL 17 + pgvector via Docker Compose;
  Makefile, GitHub Actions CI, import-linter hexagonal enforcement, pre-commit, secrets baseline,
  and SBOM target.
- M1 folder ingestion: folder-watching worker with stable-file detection, atomic move into the
  document lifecycle, streaming SHA-256, content-based MIME detection (libmagic), default security
  policy (allowlist + size limit, quarantine, dedup by hash), a SQL migration runner with the
  `ingestion_jobs` table, a Postgres ingestion job repository (plus in-memory fake), the
  `GET /api/ingestion/jobs` API, a UI ingestion jobs list, and Postgres integration tests in CI.
