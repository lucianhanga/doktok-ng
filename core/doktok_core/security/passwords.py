"""Password hashing for user login (#555, EPIC #523).

Uses stdlib :func:`hashlib.scrypt` - a memory-hard KDF - so the app takes on no new dependency
(bcrypt/argon2 are not installed). Each hash embeds its own parameters and a per-password random
salt, encoded as a single self-describing string::

    scrypt$<n>$<r>$<p>$<salt_b64>$<dk_b64>

``verify_password`` re-derives the key with the *stored* parameters (so raising the cost later does
not invalidate existing hashes) and compares in constant time. The plaintext password is never
stored; only this digest is persisted in ``users.password_hash``.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

# Interactive-login cost. n must be a power of two; n*r*p bounds memory (~16 MiB here). Tuned so a
# single verification is a few tens of milliseconds on a laptop - painful to brute-force, cheap
# enough for a login request. Stored per-hash, so these can be raised without breaking old hashes.
_N = 2**14
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16
_MAXMEM = 64 * 1024 * 1024  # scrypt's default 32 MiB cap is too low for these params; lift it.


# Password length policy (NIST SP 800-63B): a meaningful minimum, a generous maximum, and NO
# composition rules (they push users to predictable patterns). The maximum caps the scrypt work an
# attacker-supplied password can force. Applied at every human set-password path (invite accept,
# admin create/reset). Login only VERIFIES, so it never rejects a pre-existing short password.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


def validate_password(password: str) -> None:
    """Raise ``ValueError`` if ``password`` violates the length policy (#555 hardening)."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"password must be at most {MAX_PASSWORD_LENGTH} characters")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))


def hash_password(password: str) -> str:
    """Return a self-describing scrypt digest for ``password`` (see module docstring)."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM
    )
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str | None) -> bool:
    """True iff ``password`` matches the ``stored`` digest. Constant-time; never raises on bad args.

    A user with no password set (``stored`` is ``None``/empty) cannot log in with a password - this
    returns ``False`` rather than treating an empty stored hash as a match.
    """
    if not stored or not password:
        return False
    try:
        scheme, n_s, r_s, p_s, salt_b64, dk_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = _unb64(salt_b64)
        expected = _unb64(dk_b64)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(expected),
            maxmem=_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(candidate, expected)
