"""Per-tenant document lifecycle filesystem layout (ADR-0004, ADR-0007, brief section 10).

Each tenant gets its own lifecycle tree rooted at ``{files_root}/{tenant_id}/`` so a dropped file's
owner is unambiguous. Pure path computation (stdlib ``pathlib`` only) so it can live in core; the
actual filesystem IO is performed by adapters in storage/filesystem.
"""

from __future__ import annotations

from pathlib import Path


class FilesystemLayout:
    """Resolves a tenant's ingest/in.process/docs.active/docs.failed/quarantine folders."""

    def __init__(self, root: str | Path, tenant_id: str) -> None:
        self.root = Path(root)
        self.tenant_id = tenant_id
        self.base = self.root / tenant_id

    @property
    def ingest(self) -> Path:
        return self.base / "ingest"

    @property
    def in_process(self) -> Path:
        return self.base / "in.process"

    @property
    def docs_active(self) -> Path:
        return self.base / "docs.active"

    @property
    def docs_failed(self) -> Path:
        return self.base / "docs.failed"

    @property
    def quarantine(self) -> Path:
        return self.base / "quarantine"

    def ensure(self) -> None:
        for path in (
            self.ingest,
            self.in_process,
            self.docs_active,
            self.docs_failed,
            self.quarantine,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def job_workdir(self, job_id: str) -> Path:
        return self.in_process / job_id

    def job_source(self, job_id: str, suffix: str = "") -> Path:
        # ``suffix`` (the dropped file's extension) keeps the in-process file openable.
        return self.job_workdir(job_id) / f"source{suffix}"

    def failed_dir(self, job_id: str) -> Path:
        return self.docs_failed / job_id

    def quarantine_dir(self, job_id: str) -> Path:
        return self.quarantine / job_id

    def active_dir(self, document_id: str) -> Path:
        return self.docs_active / document_id
