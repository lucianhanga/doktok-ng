"""The no-egress predicate: a destination egresses iff it is non-loopback (OpenAI or remote URL)."""

from __future__ import annotations

import pytest
from doktok_core.security.egress import (
    is_loopback_url,
    purpose_requires_egress,
    url_requires_egress,
)

DEFAULT = "http://localhost:11434"


@pytest.mark.parametrize(
    ("url", "loopback"),
    [
        ("http://localhost:11434", True),
        ("http://127.0.0.1:11434", True),
        ("http://[::1]:11434", True),
        ("http://10.0.0.28:11434", False),
        ("http://ollama:11434", False),  # docker service name is NOT loopback
        ("https://api.openai.com/v1", False),
    ],
)
def test_is_loopback_url(url: str, loopback: bool) -> None:
    assert is_loopback_url(url) is loopback


def test_url_requires_egress_inherits_default_when_unset() -> None:
    assert url_requires_egress(None, default_url=DEFAULT) is False
    assert url_requires_egress("", default_url=DEFAULT) is False
    assert url_requires_egress(None, default_url="http://10.0.0.28:11434") is True


def test_purpose_requires_egress() -> None:
    # OpenAI always egresses, regardless of any URL.
    assert purpose_requires_egress("openai", None, default_url=DEFAULT) is True
    # Local Ollama (loopback default or explicit loopback) does not.
    assert purpose_requires_egress("ollama", None, default_url=DEFAULT) is False
    assert purpose_requires_egress("ollama", "http://127.0.0.1:11434", default_url=DEFAULT) is False
    # A remote Ollama URL does - this is the vector the old OpenAI-only check missed.
    assert purpose_requires_egress("ollama", "http://10.0.0.28:11434", default_url=DEFAULT) is True
