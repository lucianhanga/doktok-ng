"""DokTok NG read-only MCP server (M8, ADR-0008).

Exposes a small, allowlisted set of read-only tools over the local document store. Guarantees:

- **Read-only by construction**: every tool maps to a repository *read*; no write/delete tool.
  For defence in depth, point ``DOKTOK_MCP_DATABASE_URL`` at a Postgres role with only SELECT.
- **Tenant from configuration, never a tool argument**: the served tenant is resolved once from
  ``DOKTOK_MCP_TENANT`` (validated against the configured tenants), so a caller cannot read another
  tenant's data.
- **No migrations**: the MCP process never migrates the schema (it may run as a read-only role); the
  backend/worker own migrations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doktok_core.config import Settings, get_settings

from doktok_mcp import tools

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def resolve_tenant(configured: str, tenants: list[str]) -> str:
    """Resolve the single tenant the MCP server serves (validated, never caller-supplied)."""
    if configured:
        if configured not in tenants:
            raise ValueError(
                f"DOKTOK_MCP_TENANT {configured!r} is not a configured tenant ({tenants})"
            )
        return configured
    if len(tenants) == 1:
        return tenants[0]
    raise ValueError("set DOKTOK_MCP_TENANT: multiple tenants are configured")


def _tenants(settings: Settings) -> list[str]:
    seen: dict[str, None] = {}
    for tenant in settings.tenant_tokens.values():
        seen.setdefault(tenant, None)
    return list(seen)


def build_server(settings: Settings) -> FastMCP:
    """Wire read-only repositories and register the allowlisted tools on a FastMCP server."""
    from doktok_provider_ollama import OllamaEmbeddingProvider
    from doktok_retrieval_hybrid import HybridPostgresRetriever
    from doktok_storage_postgres import (
        Database,
        PostgresDocumentRepository,
        PostgresRecordRepository,
    )
    from mcp.server.fastmcp import FastMCP

    tenant = resolve_tenant(settings.mcp_tenant, _tenants(settings))
    db = Database(settings.mcp_database_url or settings.database_url)
    embeddings = OllamaEmbeddingProvider(
        settings.embedding_model, settings.ollama_base_url, timeout=settings.rag_timeout_seconds
    )
    retriever = HybridPostgresRetriever(db, embeddings)
    documents = PostgresDocumentRepository(db)
    records = PostgresRecordRepository(db)

    mcp = FastMCP("doktok-ng")

    @mcp.tool()
    def search_documents(query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Hybrid semantic + full-text search over the documents."""
        return tools.search_documents(retriever, tenant, query, limit)

    @mcp.tool()
    def list_documents(limit: int = 50) -> list[dict[str, Any]]:
        """List active documents with title/date/summary."""
        return tools.list_documents(documents, tenant, limit)

    @mcp.tool()
    def aggregate_records(
        merchant: str | None = None,
        record_type: str | None = None,
        direction: str | None = None,
        currency: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        """Deterministic sum/count over structured records (e.g. total spend at a merchant)."""
        return tools.aggregate_records(
            records,
            tenant,
            merchant=merchant,
            record_type=record_type,
            direction=direction,
            currency=currency,
            date_from=date_from,
            date_to=date_to,
        )

    return mcp


def main() -> None:  # pragma: no cover - stdio transport entry point
    build_server(get_settings()).run()


if __name__ == "__main__":  # pragma: no cover
    main()
