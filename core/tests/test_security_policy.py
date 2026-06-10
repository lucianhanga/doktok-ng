from doktok_contracts.schemas import SecurityDecision
from doktok_core.security.policy import DefaultSecurityPolicy


def test_allows_supported_types() -> None:
    policy = DefaultSecurityPolicy(max_file_mb=10)
    assert policy.decide("application/pdf", 1000) is SecurityDecision.ALLOW
    assert policy.decide("text/plain", 1000) is SecurityDecision.ALLOW
    assert policy.is_allowed("image/png", 1000) is True


def test_rejects_unsupported_type() -> None:
    policy = DefaultSecurityPolicy(max_file_mb=10)
    assert policy.decide("application/octet-stream", 1000) is SecurityDecision.REJECT


def test_rejects_when_too_large() -> None:
    policy = DefaultSecurityPolicy(max_file_mb=1)
    assert policy.decide("application/pdf", 2 * 1024 * 1024) is SecurityDecision.REJECT


def test_quarantines_dangerous_type() -> None:
    policy = DefaultSecurityPolicy(max_file_mb=10)
    assert policy.decide("application/x-dosexec", 1000) is SecurityDecision.QUARANTINE
    assert policy.decide("application/zip", 1000) is SecurityDecision.QUARANTINE
