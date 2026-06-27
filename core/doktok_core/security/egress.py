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
    deployment topology in ADR-0020).
    """
    return bool(key) and not no_egress


def url_requires_egress(url: str | None, *, default_url: str) -> bool:
    """Whether a purpose's effective Ollama URL would send content off-host: True iff its host is
    non-loopback. ``url`` None/"" means "inherit the default", so the default is what's checked."""
    return not is_loopback_url(url or default_url)


def purpose_requires_egress(
    provider: str, ollama_base_url: str | None, *, default_url: str
) -> bool:
    """Whether an AI purpose would move document content off this host - the single definition of
    "egress" shared by the settings boundary (PUT), the read response (GET), and the runtime sinks.

    Two vectors: provider ``openai`` (always remote), or provider ``ollama`` whose effective base
    URL points at a non-loopback host. A loopback Ollama endpoint is the only no-egress destination.
    """
    if provider == "openai":
        return True
    return url_requires_egress(ollama_base_url, default_url=default_url)
