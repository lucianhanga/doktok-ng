"""Purpose-separated subkeys for DOKTOK_SECRETS_KEY (#631, security audit F-16).

One secret used to protect four crypto domains (Fernet via bare sha256, JWT fallback, manifest
HMAC, key fingerprint), so one compromise - or one offline oracle - collapsed them all. Each
purpose now gets its own HKDF subkey.
"""

from __future__ import annotations

from doktok_core.security.keys import derive_key

_PURPOSES = ("fernet", "jwt", "manifest", "fingerprint")


def test_subkeys_are_distinct_per_purpose() -> None:
    keys = {p: derive_key("unit-test-master-key", p) for p in _PURPOSES}
    assert len(set(keys.values())) == len(_PURPOSES)


def test_derivation_is_deterministic_and_32_bytes() -> None:
    assert derive_key("k", "fernet") == derive_key("k", "fernet")
    for purpose in _PURPOSES:
        assert len(derive_key("k", purpose)) == 32
