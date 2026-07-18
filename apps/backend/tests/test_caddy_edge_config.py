"""Text-contract tests for the shipped Caddy edge config (#616, security audit F-04).

The Caddyfile is not executable in unit tests, so we assert the security-relevant properties of
its content directly. Regression being pinned: an unconditional ``header_up Authorization``
silently DISCARDED a logged-in user's JWT and replaced it with the static tenant token, nullifying
per-user RBAC (and now the ADR-0025 platform flag) through the only documented prod path. The
injection must fire only when the client sent no Authorization header of its own.
"""

from __future__ import annotations

from pathlib import Path

CADDYFILE = Path(__file__).resolve().parents[2] / "ui" / "Caddyfile"


def _lines() -> list[str]:
    return CADDYFILE.read_text(encoding="utf-8").splitlines()


def test_token_injection_is_conditional_on_missing_client_authorization() -> None:
    lines = _lines()
    # A named matcher selects requests WITHOUT an Authorization header...
    assert any("not header Authorization" in line for line in lines), (
        "no matcher for missing client Authorization"
    )
    # ...and the static token is injected only under that matcher.
    inject = next(
        line.strip() for line in lines if "Bearer {$DOKTOK_API_TOKEN}" in line and "@" in line
    )
    assert inject.startswith("header @"), f"injection is not matcher-scoped: {inject}"


def test_no_unconditional_authorization_overwrite_remains() -> None:
    for line in _lines():
        stripped = line.strip()
        assert not stripped.startswith("header_up Authorization"), (
            f"unconditional overwrite: {stripped}"
        )


def test_spa_fallback_and_tls_options_are_kept() -> None:
    text = CADDYFILE.read_text(encoding="utf-8")
    # The SPA client-side routing fallback still works...
    assert "try_files {path} /index.html" in text
    # ...and the documented TLS options (domain auto-HTTPS / self-signed internal CA) remain.
    assert "tls internal" in text
