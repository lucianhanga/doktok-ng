"""At-rest encryption for stored secrets (APP-8).

The OpenAI API key lives in ``app_settings``; encrypt it so a DB dump or read does not leak it. We
use Fernet (AES-128-CBC + HMAC) with a key derived from the deployment's ``DOKTOK_SECRETS_KEY``.
Encrypted values carry the ``enc:v1:`` marker so the reader can tell them apart from legacy
plaintext and from values written when no master key was configured.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

_MARKER = "enc:v1:"


class SecretDecryptionError(RuntimeError):
    """A stored secret is encrypted but cannot be decrypted (missing/wrong DOKTOK_SECRETS_KEY)."""


def _fernet(secrets_key: str) -> Fernet:
    # Derive a stable 32-byte Fernet key from the (arbitrary-length) master key. No salt: the master
    # key is the secret, and a deterministic derivation keeps decryption possible across restarts.
    digest = hashlib.sha256(secrets_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def is_encrypted(value: str) -> bool:
    return value.startswith(_MARKER)


def encrypt_secret(plaintext: str, secrets_key: str) -> str:
    """Encrypt ``plaintext`` if a master key is configured; otherwise return it unchanged."""
    if not secrets_key or not plaintext:
        return plaintext
    token = _fernet(secrets_key).encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _MARKER + token


def decrypt_secret(stored: str, secrets_key: str) -> str:
    """Decrypt a value written by ``encrypt_secret``. Plaintext (unmarked) values pass through."""
    if not is_encrypted(stored):
        return stored  # legacy plaintext, or written when no master key was set
    if not secrets_key:
        raise SecretDecryptionError(
            "stored secret is encrypted but DOKTOK_SECRETS_KEY is not set; cannot decrypt"
        )
    try:
        return _fernet(secrets_key).decrypt(stored[len(_MARKER) :].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretDecryptionError(
            "could not decrypt stored secret; DOKTOK_SECRETS_KEY may have changed"
        ) from exc
