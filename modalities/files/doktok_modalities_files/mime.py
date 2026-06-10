"""Content-based MIME detection (brief section 6, 11).

File type is detected from content, never from the filename extension, because extensions are
untrusted. Backed by libmagic via python-magic. libmagic must be installed on the host
(macOS: ``brew install libmagic``; Debian/Ubuntu: ``apt-get install libmagic1``).
"""

from __future__ import annotations


class LibmagicMimeDetector:
    """``MimeDetector`` using libmagic to inspect file content."""

    def __init__(self) -> None:
        import magic  # imported lazily so the package is importable without libmagic present

        self._magic = magic.Magic(mime=True)

    def detect(self, path: str) -> str:
        mime = self._magic.from_file(path)
        # libmagic may append charset info for text; keep only the media type.
        return mime.split(";")[0].strip()
