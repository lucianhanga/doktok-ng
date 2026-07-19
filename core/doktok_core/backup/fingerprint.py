"""Non-reversible fingerprint of DOKTOK_SECRETS_KEY for the portable backup manifest (Phase 1).

The manifest must let a LATER restore warn when the target host's DOKTOK_SECRETS_KEY differs from
the one that produced the archive (otherwise the Fernet-encrypted OpenAI key inside the dumped
app_settings would be silently undecryptable). We must NOT store the key. Instead we store an HMAC
of a fixed, public label keyed by the secrets key: same key -> same fingerprint, and the
fingerprint cannot be reversed to the key. An empty key (plaintext dev mode) yields an empty one.
"""

from __future__ import annotations

import hashlib
import hmac

from doktok_core.security.keys import derive_key

# A fixed, non-secret domain-separation label. Changing it changes every fingerprint; keep stable.
_FINGERPRINT_LABEL = b"doktok-portable-backup-secrets-key-fingerprint-v1"


def secrets_key_fingerprint(secrets_key: str) -> str:
    """Return a stable, non-reversible hex fingerprint of ``secrets_key`` (HMAC-SHA256 of a fixed
    label, keyed by the purpose-separated fingerprint subkey #631 - a captured archive is no
    longer an offline oracle against the raw key). Empty string when no key is configured.
    Never logs or returns the key itself."""
    if not secrets_key:
        return ""
    return hmac.new(
        derive_key(secrets_key, "fingerprint"), _FINGERPRINT_LABEL, hashlib.sha256
    ).hexdigest()
