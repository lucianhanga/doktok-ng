import pytest
from doktok_core.security.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    validate_password,
    verify_password,
)


def test_validate_password_accepts_policy_compliant() -> None:
    validate_password("a" * MIN_PASSWORD_LENGTH)  # no raise
    validate_password("a" * MAX_PASSWORD_LENGTH)


def test_validate_password_rejects_too_short() -> None:
    with pytest.raises(ValueError, match="at least"):
        validate_password("a" * (MIN_PASSWORD_LENGTH - 1))


def test_validate_password_rejects_too_long() -> None:
    with pytest.raises(ValueError, match="at most"):
        validate_password("a" * (MAX_PASSWORD_LENGTH + 1))


def test_hash_is_self_describing_scrypt() -> None:
    digest = hash_password("correct horse battery staple")
    assert digest.startswith("scrypt$")
    assert len(digest.split("$")) == 6


def test_new_hashes_use_the_owasp_current_work_factor() -> None:
    # F-30 (#642): N=2^17 (~128 MiB) per OWASP guidance - ~8x the offline-cracking cost of the
    # old 2^14 per guess after a DB leak. Hashes are self-describing, so old ones still verify.
    digest = hash_password("correct horse battery staple")
    assert digest.split("$")[1] == str(2**17)


def test_pre_change_2pow14_hash_still_verifies() -> None:
    # A hash written before the work-factor raise carries its own parameters and still verifies.
    import base64
    import hashlib

    salt = b"0123456789abcdef"
    dk = hashlib.scrypt(b"s3cret!", salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=2**26)
    stored = (
        "scrypt$16384$8$1$"
        + base64.urlsafe_b64encode(salt).decode()
        + "$"
        + base64.urlsafe_b64encode(dk).decode()
    )
    assert verify_password("s3cret!", stored) is True
    assert verify_password("nope", stored) is False


def test_hash_is_salted_and_unique() -> None:
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b  # random per-password salt


def test_verify_accepts_correct_password() -> None:
    digest = hash_password("s3cret!")
    assert verify_password("s3cret!", digest) is True


def test_verify_rejects_wrong_password() -> None:
    digest = hash_password("s3cret!")
    assert verify_password("nope", digest) is False


def test_verify_rejects_empty_and_missing() -> None:
    digest = hash_password("s3cret!")
    assert verify_password("", digest) is False
    assert verify_password("s3cret!", None) is False
    assert verify_password("s3cret!", "") is False


def test_verify_rejects_malformed_stored_hash() -> None:
    assert verify_password("s3cret!", "not-a-hash") is False
    assert verify_password("s3cret!", "bcrypt$foo$bar") is False


def test_hash_rejects_empty_password() -> None:
    import pytest

    with pytest.raises(ValueError):
        hash_password("")
