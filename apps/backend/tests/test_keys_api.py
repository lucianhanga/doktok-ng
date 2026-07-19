"""Call-site checks for the purpose-separated subkeys (#631, F-16)."""

from __future__ import annotations

import base64
import hashlib
import hmac
from pathlib import Path

from doktok_contracts.schemas import BackupManifestMember
from doktok_core.backup.export import _manifest_hmac  # noqa: SLF001 - contract under test
from doktok_core.backup.fingerprint import secrets_key_fingerprint
from doktok_core.security.keys import derive_key


def test_manifest_hmac_uses_the_manifest_subkey() -> None:
    members = [BackupManifestMember(name="a", sha256="b" * 64, size=1)]
    expected = hmac.new(
        derive_key("k", "manifest"), ("a:" + "b" * 64).encode(), hashlib.sha256
    ).hexdigest()
    assert _manifest_hmac("k", members) == expected


def test_fingerprint_uses_the_fingerprint_subkey() -> None:
    expected = hmac.new(
        derive_key("k", "fingerprint"),
        b"doktok-portable-backup-secrets-key-fingerprint-v1",
        hashlib.sha256,
    ).hexdigest()
    assert secrets_key_fingerprint("k") == expected


def test_jwt_fallback_secret_is_derived_not_raw() -> None:
    from doktok_api.dependencies import effective_jwt_secret
    from doktok_core.config import Settings

    settings = Settings(  # type: ignore[call-arg]
        env="test",
        auth_jwt_secret="",
        secrets_key="master-key-123",  # pragma: allowlist secret
        _env_file=None,
    )
    secret = effective_jwt_secret(settings)
    assert secret != "master-key-123"
    assert secret == base64.urlsafe_b64encode(derive_key("master-key-123", "jwt")).decode()


def test_dedicated_jwt_secret_wins_over_the_fallback() -> None:
    from doktok_api.dependencies import effective_jwt_secret
    from doktok_core.config import Settings

    settings = Settings(  # type: ignore[call-arg]
        env="test",
        auth_jwt_secret="dedicated-secret",
        secrets_key="master-key-123",  # pragma: allowlist secret
        _env_file=None,
    )
    assert effective_jwt_secret(settings) == "dedicated-secret"


def test_prod_template_documents_the_dedicated_jwt_secret() -> None:
    text = Path(".env.production.example").read_text(encoding="utf-8")
    assert "DOKTOK_AUTH_JWT_SECRET" in text
