"""No-egress guardrail (ADR-0006/ADR-0008).

DokTok NG is local-first and defaults to no network egress. The only outbound calls are to a local
model runtime (Ollama). This module decides whether a configured base URL is a loopback address, so
startup can refuse a remote model endpoint while ``DOKTOK_NO_EGRESS`` is on - turning the documented
posture into an enforced one instead of a comment.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_LOOPBACK_HOSTNAMES = frozenset({"localhost", "ip6-localhost"})


def is_loopback_url(url: str) -> bool:
    """True if ``url``'s host is loopback (localhost / 127.0.0.0/8 / ::1)."""
    host = urlparse(url).hostname
    if host is None:
        return False
    if host in _LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
