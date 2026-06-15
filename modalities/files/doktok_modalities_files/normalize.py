"""Office-document -> PDF normalization via a local Gotenberg container (M8.x #313).

Gotenberg (https://gotenberg.dev, MIT, Docker-published) wraps headless LibreOffice. It runs locally
in the compose stack, so document content never leaves the host. The converted PDF then flows
through the existing canonical PDF path (extract / render / OCR / preview).
"""

from __future__ import annotations

from pathlib import Path

import httpx


class GotenbergNormalizer:
    """``DocumentNormalizer`` converting office formats to PDF via Gotenberg's LibreOffice route."""

    def __init__(self, base_url: str, *, timeout: float = 180.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def to_pdf(self, path: str, mime: str) -> bytes:
        source = Path(path)
        with source.open("rb") as handle:
            response = httpx.post(
                f"{self._base_url}/forms/libreoffice/convert",
                files={"files": (source.name, handle, mime)},
                timeout=self._timeout,
            )
        response.raise_for_status()
        return response.content
