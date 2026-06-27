"""The no-egress predicate: a destination egresses iff it is non-loopback (OpenAI or remote URL)."""

from __future__ import annotations

import pytest
from doktok_core.security.egress import (
    EgressBlocked,
    EgressBlockedError,
    effective_no_egress,
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


def test_effective_no_egress_resolution() -> None:
    # In-app toggle wins over the env default when set.
    assert effective_no_egress(False, env_default=True, lock=False) is False
    assert effective_no_egress(True, env_default=False, lock=False) is True
    # Never set -> the env default applies.
    assert effective_no_egress(None, env_default=True, lock=False) is True
    assert effective_no_egress(None, env_default=False, lock=False) is False
    # A host lock forces it on regardless of the stored toggle or the env default.
    assert effective_no_egress(False, env_default=False, lock=True) is True


def test_egress_blocked_fails_loud_on_every_method() -> None:
    blocked = EgressBlocked("Data pipeline")
    assert "DOKTOK_NO_EGRESS" in blocked.message
    # Every provider surface raises rather than silently doing nothing or substituting.
    for call in (
        lambda: blocked.extract("x"),
        lambda: blocked.classify("x", []),
        lambda: blocked.complete("x"),
        lambda: blocked.embed(["x"]),
    ):
        with pytest.raises(EgressBlockedError):
            call()
