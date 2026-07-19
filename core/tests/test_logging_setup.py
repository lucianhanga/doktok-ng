"""Structured JSON logging + secret redaction (APP-12)."""

from __future__ import annotations

import json
import logging

from doktok_core.logging_setup import (
    JsonLogFormatter,
    redact,
    request_id_var,
    tenant_id_var,
)


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("doktok.test", logging.INFO, __file__, 1, msg, None, None)


def test_json_formatter_emits_structured_fields() -> None:
    out = json.loads(JsonLogFormatter().format(_record("hello")))
    assert out["level"] == "INFO" and out["logger"] == "doktok.test" and out["msg"] == "hello"


def test_json_formatter_includes_correlation_when_set() -> None:
    request_id_var.set("req-123")
    tenant_id_var.set("tenant-a")
    try:
        out = json.loads(JsonLogFormatter().format(_record("x")))
    finally:
        request_id_var.set("")
        tenant_id_var.set("")
    assert out["request_id"] == "req-123" and out["tenant_id"] == "tenant-a"


def test_secrets_are_redacted() -> None:
    assert "[REDACTED]" in redact("key is sk-ABCD1234efgh and done")
    assert "sk-ABCD1234efgh" not in redact("sk-ABCD1234efgh")
    assert redact("Authorization: Bearer abc.def-123") == "Authorization: [REDACTED]"


def test_dsn_credentials_are_redacted_but_structure_kept() -> None:
    # F-31 (#643): a connection string's user:pass must never reach the logs; the host/db shape
    # stays for debuggability.
    out = redact("connecting to postgresql://doktok:s3cret-value@db:5432/doktok failed")
    assert "s3cret-value" not in out
    assert "postgresql://[REDACTED]@db:5432/doktok" in out


def test_jwt_shaped_strings_are_redacted() -> None:
    # F-31 (#643): a raw JWT (not preceded by 'Bearer') must not survive into a log line.
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.c2lnbmF0dXJlLXZhbHVl"
    assert jwt not in redact(f"token was {jwt} ok")
    assert "[REDACTED]" in redact(f"token was {jwt} ok")


def test_text_mode_formatter_redacts_message_args_and_traceback() -> None:
    # F-31 (#643): the default text formatter previously installed NO redaction - a secret in the
    # message args or inside an exception traceback landed unmasked.
    import io

    from doktok_core.logging_setup import configure_logging

    stream = io.StringIO()
    configure_logging(json_format=False, level="INFO")
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.stream = stream

    logger = logging.getLogger("doktok.test.f31")
    logger.info("dsn is %s", "postgresql://u:s3cret@h/db")
    try:
        raise RuntimeError("boom for sk-TRACESECRET1")
    except RuntimeError:
        logger.exception("failed")
    out = stream.getvalue()
    assert "s3cret" not in out
    assert "sk-TRACESECRET1" not in out


def test_redaction_applies_in_formatter() -> None:
    out = json.loads(JsonLogFormatter().format(_record("using sk-SECRETvalue99 now")))
    assert "sk-SECRETvalue99" not in out["msg"] and "[REDACTED]" in out["msg"]
