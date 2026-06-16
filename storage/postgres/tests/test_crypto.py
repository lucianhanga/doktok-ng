"""At-rest secret encryption (APP-8)."""

from __future__ import annotations

import pytest
from doktok_storage_postgres.crypto import (
    SecretDecryptionError,
    decrypt_secret,
    encrypt_secret,
    is_encrypted,
)

KEY = "master-secret-123"


def test_roundtrip_with_key() -> None:
    token = encrypt_secret("sk-openai-abc", KEY)
    assert is_encrypted(token) and "sk-openai-abc" not in token
    assert decrypt_secret(token, KEY) == "sk-openai-abc"


def test_no_master_key_stores_plaintext() -> None:
    # Local-dev default: without a master key, values pass through unchanged (decrypt is a no-op).
    assert encrypt_secret("sk-x", "") == "sk-x"
    assert decrypt_secret("sk-x", "") == "sk-x"


def test_empty_value_is_passthrough() -> None:
    assert encrypt_secret("", KEY) == ""


def test_wrong_key_raises_clear_error() -> None:
    token = encrypt_secret("sk-x", KEY)
    with pytest.raises(SecretDecryptionError, match="DOKTOK_SECRETS_KEY"):
        decrypt_secret(token, "different-key")


def test_encrypted_value_without_key_raises() -> None:
    token = encrypt_secret("sk-x", KEY)
    with pytest.raises(SecretDecryptionError):
        decrypt_secret(token, "")


def test_legacy_plaintext_passes_through_even_with_key() -> None:
    # A value stored before APP-8 (no marker) is returned as-is.
    assert decrypt_secret("sk-legacy-plaintext", KEY) == "sk-legacy-plaintext"
