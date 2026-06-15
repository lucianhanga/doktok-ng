"""Default security policy for ingested files (ADR-0006).

Content is untrusted. The policy decides whether a file is allowed, should be quarantined (looks
dangerous), or rejected (unsupported type or too large). MIME is detected by content, not extension.
"""

from __future__ import annotations

from doktok_contracts.schemas import SecurityDecision

# Types DokTok NG can process (M1 allowlist; extraction support grows in M2-M3).
DEFAULT_ALLOWED_MIMES: frozenset[str] = frozenset(
    {
        "text/plain",
        "text/markdown",
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/webp",
        # Office OOXML (M8.x #313): converted to PDF on ingest via the document normalizer.
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)

# Types that should be isolated rather than merely rejected.
DEFAULT_DANGEROUS_MIMES: frozenset[str] = frozenset(
    {
        "application/x-dosexec",
        "application/x-mach-binary",
        "application/x-executable",
        "application/x-sharedlib",
        "application/x-msdownload",
        "application/x-elf",
        "application/zip",
        "application/x-tar",
        "application/gzip",
        "application/x-7z-compressed",
        "application/x-rar",
        "application/java-archive",
    }
)


class DefaultSecurityPolicy:
    """MIME allowlist plus a maximum file size."""

    def __init__(
        self,
        *,
        max_file_mb: int = 200,
        allowed_mimes: frozenset[str] = DEFAULT_ALLOWED_MIMES,
        dangerous_mimes: frozenset[str] = DEFAULT_DANGEROUS_MIMES,
    ) -> None:
        self.max_file_bytes = max_file_mb * 1024 * 1024
        self._allowed = allowed_mimes
        self._dangerous = dangerous_mimes

    def is_allowed(self, mime: str, size_bytes: int) -> bool:
        return self.decide(mime, size_bytes) is SecurityDecision.ALLOW

    def decide(self, mime: str, size_bytes: int) -> SecurityDecision:
        if size_bytes > self.max_file_bytes:
            return SecurityDecision.REJECT
        if mime in self._dangerous:
            return SecurityDecision.QUARANTINE
        if mime in self._allowed:
            return SecurityDecision.ALLOW
        return SecurityDecision.REJECT
