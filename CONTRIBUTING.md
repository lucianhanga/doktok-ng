# Contributing to DokTok NG

DokTok NG is built to be maintainable by one developer plus coding agents. The workflow is deliberately
simple and disciplined.

## Workflow (milestone-driven, ticket-driven)

1. Work is organized into milestones **M0-M10** (see [docs/milestones/M0-M10.md](docs/milestones/M0-M10.md)).
2. Each milestone is tracked by a GitHub milestone and the **DokTok NG Roadmap** project board.
3. Each unit of work is a GitHub issue (a `task`), labeled with its area and `milestone-mX`.
4. One **branch per ticket**, one **PR per ticket**, merged into `main`.
5. Do not merge code that leaves the system unusable. Every milestone must ship a runnable system.
6. Do not implement multiple milestones in one pass.

## Branch naming

```
mX/<short-slug>          e.g. m0/backend-health-endpoint
fix/<short-slug>
docs/<short-slug>
```

## Commits

Use Conventional Commits:

```
feat: ...      a new feature
fix: ...       a bug fix
docs: ...      documentation only
chore: ...     tooling/build/repo
test: ...      tests only
refactor: ...  no behavior change
```

## Quality gate (from M0 onward)

Before opening a PR, the relevant checks must pass:

- Python: `ruff` lint, `ruff format`, `mypy` typecheck, `pytest`, `import-linter` (hexagonal arch).
- Frontend: typecheck, lint, Vitest.
- A single `make check` runs the full suite once it exists.

## Architecture rules

- Core domain (`core/doktok_core`) depends only on **ports** (interfaces) in `contracts/`.
- Infrastructure lives in **adapters** (`providers/`, `storage/`, `modalities/`, `retrieval/`).
- `import-linter` enforces the dependency direction; do not import adapters from core.
- Treat all files, extracted text, model output, tool output, and MCP I/O as untrusted.

## Security

See [SECURITY.md](SECURITY.md). No remote providers or network egress by default. All sensitive
operations are audited.
