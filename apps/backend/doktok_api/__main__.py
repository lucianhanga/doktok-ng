"""Backend entrypoint: ``python -m doktok_api`` (binds settings.bind_host)."""

from __future__ import annotations

import os

import uvicorn
from doktok_core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "doktok_api.main:app",
        host=settings.bind_host,
        port=int(os.environ.get("DOKTOK_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
