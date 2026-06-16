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


def openai_egress_allowed(*, key: str, no_egress: bool) -> bool:
    """Whether the OpenAI provider may be used: a key is configured AND egress is permitted.

    Selecting OpenAI sends document content off the host, so it is refused while no-egress is on
    (``DOKTOK_NO_EGRESS``) - the loopback check only covers the local Ollama endpoint, not OpenAI.
    Set ``DOKTOK_NO_EGRESS=false`` to opt into remote enrichment/RAG (ADR-0006; the hybrid
    deployment topology in ADR-0020). Callers that select OpenAI but get ``False`` here must fall
    back to the local default rather than silently egressing.
    """
    return bool(key) and not no_egress
