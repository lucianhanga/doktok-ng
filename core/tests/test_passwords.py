from doktok_core.security.passwords import hash_password, verify_password


def test_hash_is_self_describing_scrypt() -> None:
    digest = hash_password("correct horse battery staple")
    assert digest.startswith("scrypt$")
    assert len(digest.split("$")) == 6


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
