"""Worker entrypoint: ``python -m doktok_worker`` or ``uv run doktok-worker``."""

from __future__ import annotations

import logging
import signal
from types import FrameType

from doktok_core.config import get_settings
from doktok_core.logging_setup import configure_logging

from doktok_worker.composition import build_services
from doktok_worker.worker import IngestionWorker


def _install_sigterm_handler() -> None:
    """Convert SIGTERM into KeyboardInterrupt so a `make`/`kill` stop runs the same graceful
    shutdown path as Ctrl-C (atexit alone does not fire on SIGTERM) - this is what lets the OCR
    pool be torn down instead of leaked as orphan processes."""

    def _handle(_signum: int, _frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle)


def repair(*, dry_run: bool, check_hashes: bool = False) -> int:
    """Reconcile active documents against on-disk artifacts (APP-C2), re-queuing re-derivable gaps
    and reporting unrecoverable ones. With check_hashes, also verify each original's sha256 against
    the row (APP-D2). Builds only the DB repos (no OCR pools)."""
    from pathlib import Path

    from doktok_core.documents.repair import repair_documents
    from doktok_storage_filesystem import Sha256HashService
    from doktok_storage_postgres import (
        Database,
        PostgresDocumentRepository,
        PostgresFeatureRepository,
    )

    from doktok_worker.composition import tenant_ids

    log = logging.getLogger("doktok.worker")
    settings = get_settings()
    db = Database(settings.database_url)
    hasher = Sha256HashService().sha256 if check_hashes else None
    problems = 0
    try:
        doc_repo = PostgresDocumentRepository(db)
        feat_repo = PostgresFeatureRepository(db)
        for tenant in tenant_ids(settings):
            report = repair_documents(
                document_repo=doc_repo,
                feature_repo=feat_repo,
                exists=lambda p: Path(p).exists(),
                tenant_id=tenant,
                dry_run=dry_run,
                compute_sha256=hasher,
            )
            log.info("repair[%s]%s %s", tenant, " (dry-run)" if dry_run else "", report.summary())
            for label, ids in (
                ("MISSING ORIGINAL", report.unrecoverable),
                ("CORRUPTED", report.corrupted),
            ):
                if ids:
                    problems += len(ids)
                    log.warning(
                        "repair[%s]: %d document(s) %s: %s", tenant, len(ids), label, ", ".join(ids)
                    )
    finally:
        db.close()
    # Non-zero exit if anything is unrecoverable/corrupted, so an operator/automation notices.
    return 1 if problems else 0


def rebuild_registry(*, tenant: str | None, dry_run: bool) -> int:
    """Re-register documents from surviving files_root manifests (#749 files-ahead-of-DB recovery),
    re-queuing ONLY the enrichment features (extract counts as done - the artifacts are on disk, so
    OCR never re-runs). Builds only the DB repos (no OCR pools)."""
    import json
    from pathlib import Path
    from typing import Any

    from doktok_core.documents.rebuild_registry import rebuild_registry as _rebuild
    from doktok_core.features.processors import ENRICHMENT_STAGES
    from doktok_storage_postgres import (
        Database,
        PostgresDocumentRepository,
        PostgresFeatureRepository,
    )

    from doktok_worker.composition import tenant_ids

    log = logging.getLogger("doktok.worker")
    settings = get_settings()
    db = Database(settings.database_url)
    root = Path(settings.files_root)
    problems = 0
    try:
        doc_repo = PostgresDocumentRepository(db)
        feat_repo = PostgresFeatureRepository(db)
        with db.connection() as conn:
            known_tenants = {row[0] for row in conn.execute("select id from tenants").fetchall()}

        def load(tenant_id: str) -> list[tuple[str, dict[str, Any] | None]]:
            base = root / tenant_id / "docs.active"
            if not base.is_dir():
                return []
            out: list[tuple[str, dict[str, Any] | None]] = []
            for entry in sorted(base.iterdir()):
                if not entry.is_dir():
                    continue
                try:
                    out.append(
                        (str(entry), json.loads((entry / "manifest.json").read_text("utf-8")))
                    )
                except (OSError, json.JSONDecodeError):
                    out.append((str(entry), None))
            return out

        for tid in [tenant] if tenant else tenant_ids(settings):
            if tid not in known_tenants:
                log.warning(
                    "rebuild-registry[%s]: tenant not in the DB - skipping (create it first)", tid
                )
                problems += 1
                continue
            report = _rebuild(
                document_repo=doc_repo,
                feature_repo=feat_repo,
                load_manifests=load,
                tenant_id=tid,
                stages=ENRICHMENT_STAGES,
                dry_run=dry_run,
            )
            log.info(
                "rebuild-registry[%s]%s %s", tid, " (dry-run)" if dry_run else "", report.summary()
            )
            if report.orphans:
                problems += len(report.orphans)
                log.warning(
                    "rebuild-registry[%s]: %d dir(s) WITHOUT a usable manifest: %s",
                    tid,
                    len(report.orphans),
                    ", ".join(report.orphans),
                )
    finally:
        db.close()
    # Non-zero exit if anything is orphaned/unknown, so an operator/automation notices.
    return 1 if problems else 0


def quiesce(*, enabled: bool) -> int:
    """Toggle maintenance/quiesce mode (APP-C3): the running worker stops starting new ingestion +
    reconcile work while on. Use around a backup: `quiesce` -> snapshot -> `quiesce --off`."""
    from doktok_storage_postgres import Database, PostgresAppSettingsRepository

    log = logging.getLogger("doktok.worker")
    settings = get_settings()
    db = Database(settings.database_url)
    try:
        PostgresAppSettingsRepository(db, secrets_key=settings.secrets_key).set_maintenance_mode(
            enabled=enabled
        )
    finally:
        db.close()
    log.info("maintenance mode set %s", "ON (worker pausing new work)" if enabled else "OFF")
    return 0


def main() -> None:
    import sys

    settings = get_settings()
    configure_logging(json_format=settings.log_format == "json", level=settings.log_level)
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "repair":
        raise SystemExit(
            repair(dry_run="--dry-run" in sys.argv, check_hashes="--check-hashes" in sys.argv)
        )
    if command == "rebuild-registry":
        tenant = sys.argv[sys.argv.index("--tenant") + 1] if "--tenant" in sys.argv else None
        raise SystemExit(rebuild_registry(tenant=tenant, dry_run="--dry-run" in sys.argv))
    if command == "quiesce":
        raise SystemExit(quiesce(enabled="--off" not in sys.argv))
    _install_sigterm_handler()
    log = logging.getLogger("doktok.worker")
    (
        services,
        reconciler,
        projection_runner,
        db,
        ocr_reload,
        ai_reload,
        post_reconcile,
        cleanup,
        heartbeat,
        is_quiesced,
    ) = build_services(settings)
    if not services:
        log.warning(
            "no tenants configured (DOKTOK_TENANT_TOKENS is empty); the worker has nothing to watch"
        )
    worker = IngestionWorker(
        services,
        stability_seconds=settings.file_stability_seconds,
        concurrency=settings.ingest_concurrency,
        reconciler=reconciler,
        stale_job_minutes=settings.stale_job_minutes,
        projection_runner=projection_runner,
        ocr_reload=ocr_reload,
        ai_reload=ai_reload,
        post_reconcile=post_reconcile,
        heartbeat=heartbeat,
        is_quiesced=is_quiesced,
    )
    if settings.ingest_concurrency > 1:
        log.info("processing up to %d documents in parallel", settings.ingest_concurrency)
    # Activity log (M15 #373): a 'service.started' row per tenant so a restart is visible.
    try:
        from doktok_contracts.schemas import AuditEventType
        from doktok_core.audit.logger import record_activity
        from doktok_storage_postgres import PostgresAuditLogRepository

        audit_repo = PostgresAuditLogRepository(db)
        for tenant in dict.fromkeys(settings.tenant_tokens.values()):
            record_activity(
                audit_repo,
                tenant,
                AuditEventType.SERVICE_STARTED,
                actor="worker",
                actor_kind="system",
                description="Worker started",
            )
    except Exception:  # noqa: BLE001 - a startup activity row must never block the worker
        log.warning("failed to record worker-started activity", exc_info=True)
    try:
        worker.run_forever()
    except KeyboardInterrupt:  # pragma: no cover
        log.info("shutting down")
    finally:
        cleanup()  # tear down the OCR pool so its spawn workers do not leak as orphans
        db.close()


if __name__ == "__main__":
    main()
