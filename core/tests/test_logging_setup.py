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


def test_redaction_applies_in_formatter() -> None:
    out = json.loads(JsonLogFormatter().format(_record("using sk-SECRETvalue99 now")))
    assert "sk-SECRETvalue99" not in out["msg"] and "[REDACTED]" in out["msg"]
