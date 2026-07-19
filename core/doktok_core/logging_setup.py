"""Structured logging with request/tenant correlation and secret redaction (APP-12).

Shared by the backend and the worker. ``configure_logging`` installs either a JSON formatter (for a
log pipeline) or the human-readable text format. The JSON records carry ``request_id`` and
``tenant_id`` from contextvars the request layer sets, so a request can be traced across log lines.
A redaction pass masks obvious secrets (API keys, bearer tokens, DSN credentials, JWTs) in BOTH
formatters so they never reach the logs.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
tenant_id_var: ContextVar[str] = ContextVar("tenant_id", default="")

# Mask obvious secrets anywhere in a log message (F-31): OpenAI-style keys, bearer tokens, DSN
# credentials, and JWT-shaped strings. Raw hex is deliberately NOT masked - uuid4/sha256 ids fill
# the logs and are not secrets; over-redaction would destroy debuggability.
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|[Bb]earer\s+[A-Za-z0-9._\-]+)")
_DSN_RE = re.compile(r"((?:postgresql?|mysql|mongodb(?:\+srv)?|redis)://)[^:\s/]+:[^@\s]+@")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")


def redact(text: str) -> str:
    text = _SECRET_RE.sub("[REDACTED]", text)
    text = _DSN_RE.sub(r"\1[REDACTED]@", text)  # keep the dsn://...@host shape, mask credentials
    return _JWT_RE.sub("[REDACTED]", text)


class _RedactingTextFormatter(logging.Formatter):
    """Text formatter redacting the FINAL rendered line (F-31, #643): message, %-args, and the
    exception traceback - formatException renders only at format time, so a logging Filter could
    never reach it."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        if rid := request_id_var.get():
            payload["request_id"] = rid
        if tid := tenant_id_var.get():
            payload["tenant_id"] = tid
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(*, json_format: bool, level: str = "INFO") -> None:
    """Install the root log handler. Idempotent (replaces existing handlers)."""
    handler = logging.StreamHandler(sys.stdout)
    if json_format:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            _RedactingTextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
