"""Validate/normalize raw extracted transactions into typed records (M6.3).

Money is stored as **integer minor units** (cents) - never float - so SUM is exact. Merchant names
are normalized to a fuzzy-match key. Pure and deterministic, so it is unit-tested without a model.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime

from doktok_contracts.media import ExtractedTransaction
from doktok_contracts.schemas import ExtractedRecord

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def parse_amount_minor(raw: str | None) -> int | None:
    """Parse an amount string to integer minor units (cents). Handles ``,``/``.`` separators."""
    if not raw:
        return None
    text = re.sub(r"[^\d.,\-]", "", raw.strip())
    if not text or text in {"-", ".", ","}:
        return None
    negative = text.startswith("-")
    text = text.lstrip("-")
    if "," in text and "." in text:
        # the right-most separator is the decimal point
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        # comma alone: decimal if exactly 2 trailing digits, else a thousands separator
        text = text.replace(",", ".") if re.search(r",\d{2}$", text) else text.replace(",", "")
    try:
        minor = round(float(text) * 100)
    except ValueError:
        return None
    return -minor if negative else minor


def normalize_merchant(name: str | None) -> str | None:
    if not name:
        return None
    text = _WS.sub(" ", _PUNCT.sub(" ", name).casefold()).strip()
    return text or None


def _parse_date(raw: str | None) -> date | None:
    if not raw or not _ISO_DATE.match(raw.strip()):
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _currency(raw: str | None) -> str | None:
    if not raw:
        return None
    code = raw.strip().upper()
    return code if len(code) == 3 and code.isalpha() else None


def _direction(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip().lower()
    return value if value in {"debit", "credit"} else None


def normalize_transaction(
    raw: ExtractedTransaction,
    *,
    tenant_id: str,
    document_id: str,
    record_type: str = "card_transaction",
) -> ExtractedRecord | None:
    """Build a typed record, or None if the row carries neither an amount nor a merchant."""
    amount_minor = parse_amount_minor(raw.amount)
    currency = _currency(raw.currency)
    if amount_minor is not None and currency is None:
        amount_minor = None  # the DB requires a currency for any stored amount
    merchant_raw = (raw.merchant or "").strip() or None
    description = (raw.description or "").strip() or None
    merchant_source = merchant_raw or description
    if amount_minor is None and not merchant_source:
        return None
    return ExtractedRecord(
        id=uuid.uuid4().hex,
        tenant_id=tenant_id,
        document_id=document_id,
        record_type=record_type,
        raw_text=(raw.raw_text or merchant_source or "").strip(),
        occurred_on=_parse_date(raw.date),
        amount_minor=amount_minor,
        currency=currency if amount_minor is not None else None,
        direction=_direction(raw.direction),
        merchant_raw=merchant_raw,
        merchant_normalized=normalize_merchant(merchant_source),
        description=description,
    )
