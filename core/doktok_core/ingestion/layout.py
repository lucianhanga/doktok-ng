"""Document lifecycle filesystem layout (ADR-0004, brief section 10).

Pure path computation (stdlib ``pathlib`` only) so it can live in core; the actual filesystem IO is
performed by adapters in storage/filesystem.
"""

from __future__ import annotations

from pathlib import Path


class FilesystemLayout:
    """Resolves the ingest/in.process/docs.active/docs.failed/quarantine folders under a root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @property
    def ingest(self) -> Path:
        return self.root / "ingest"

    @property
    def in_process(self) -> Path:
        return self.root / "in.process"

    @property
    def docs_active(self) -> Path:
        return self.root / "docs.active"

    @property
    def docs_failed(self) -> Path:
        return self.root / "docs.failed"

    @property
    def quarantine(self) -> Path:
        return self.root / "quarantine"

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

    def job_source(self, job_id: str) -> Path:
        return self.job_workdir(job_id) / "source"

    def failed_dir(self, job_id: str) -> Path:
        return self.docs_failed / job_id

    def quarantine_dir(self, job_id: str) -> Path:
        return self.quarantine / job_id

    def active_dir(self, document_id: str) -> Path:
        return self.docs_active / document_id
