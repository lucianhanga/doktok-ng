"""Integration tests for the projection recompute queue (ADR-0016, M7.1; test* tenants only)."""

from __future__ import annotations

from doktok_storage_postgres import Database, PostgresProjectionRequestRepository

TENANT = "test-a"


def test_request_is_idempotent_per_tenant(db: Database) -> None:
    repo = PostgresProjectionRequestRepository(db)
    repo.request(TENANT)
    repo.request(TENANT)  # repeat press while one is pending -> no second row

    assert repo.has_pending(TENANT) is True
    first = repo.claim_next()
    assert first is not None and first.tenant_id == TENANT
    assert repo.claim_next() is None  # only one was ever queued


def test_claim_then_complete_clears_the_queue(db: Database) -> None:
    repo = PostgresProjectionRequestRepository(db)
    repo.request(TENANT)

    claimed = repo.claim_next()
    assert claimed is not None and claimed.status == "running"
    assert repo.has_pending(TENANT) is True  # still queued (running) until completed
    repo.complete(claimed.id)
    assert repo.has_pending(TENANT) is False
    # A new request can be enqueued once the prior one is cleared.
    repo.request(TENANT)
    assert repo.has_pending(TENANT) is True
    repo.complete(repo.claim_next().id)  # type: ignore[union-attr]


def test_claim_is_fifo_across_tenants(db: Database) -> None:
    repo = PostgresProjectionRequestRepository(db)
    repo.request("test-a")
    repo.request("test-b")

    first = repo.claim_next()
    second = repo.claim_next()
    assert first is not None and second is not None
    assert {first.tenant_id, second.tenant_id} == {"test-a", "test-b"}
    assert first.requested_at <= second.requested_at  # oldest first
    repo.complete(first.id)
    repo.complete(second.id)
