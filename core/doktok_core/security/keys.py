"""Purpose-separated subkeys for the deployment master secret (#631, security audit F-16).

``DOKTOK_SECRETS_KEY`` used to protect four crypto domains directly - the at-rest Fernet key
(bare ``sha256(key)``), the session-JWT fallback secret, the backup-manifest HMAC, and the
archive-carried key fingerprint - so a single compromise (or a single offline oracle: any
captured JWT, or the known-label fingerprint in every backup archive) collapsed them all.

Each purpose now gets its own HKDF-SHA256 subkey (stdlib-only extract+expand, deterministic - a
32-byte output fits one expand block). Callers use this module instead of hashing the master key
directly; there is deliberately no "master" export so new code cannot accidentally reuse it.
"""

from __future__ import annotations

import hashlib
import hmac

# HKDF extract salt. Fixed and public; changing it changes every subkey, so it must stay stable
# for the life of the data encrypted with them.
_SALT = b"doktok-ng-key-separation-v1"


def derive_key(secrets_key: str, purpose: str) -> bytes:
    """The 32-byte HKDF-SHA256 subkey for one crypto purpose (``fernet`` | ``jwt`` | ``manifest``
    | ``fingerprint``). Deterministic for a given (key, purpose) and independent across purposes:
    recovering one subkey does not help against the others beyond the master key's own entropy.
    """
    prk = hmac.new(_SALT, secrets_key.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(prk, purpose.encode("utf-8"), hashlib.sha256).digest()
