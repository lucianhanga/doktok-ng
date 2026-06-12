"""OpenAI adapter tests (httpx mocked - no network)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
from doktok_provider_openai import (
    OpenAiChatModelProvider,
    OpenAiMetadataExtractor,
    OpenAiRecordExtractor,
)


def _resp(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )


def test_chat_non_reasoning_uses_temperature() -> None:
    with patch("doktok_provider_openai.client.httpx.post", return_value=_resp("hello")) as post:
        out = OpenAiChatModelProvider("gpt-4o-mini", "k").complete("hi")
    assert out == "hello"
    body = post.call_args.kwargs["json"]
    assert body["temperature"] == 0 and "reasoning_effort" not in body
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer k"


def test_chat_reasoning_model_uses_effort_not_temperature() -> None:
    with patch("doktok_provider_openai.client.httpx.post", return_value=_resp("x")) as post:
        OpenAiChatModelProvider("gpt-5-mini", "k", reasoning_effort="medium").complete("hi")
    body = post.call_args.kwargs["json"]
    assert body["reasoning_effort"] == "medium" and "temperature" not in body


def test_metadata_extraction() -> None:
    content = (
        '{"title":"Invoice","document_date":"2024-02-03",'
        '"document_location":"Berlin","summary":"S"}'
    )
    with patch("doktok_provider_openai.client.httpx.post", return_value=_resp(content)):
        md = OpenAiMetadataExtractor("gpt-4o-mini", "k").extract("body text")
    assert md.title == "Invoice" and md.location == "Berlin" and md.document_date == "2024-02-03"


def test_record_extraction() -> None:
    content = (
        '{"transactions":[{"date":"2024-02-03","merchant":"Block House",'
        '"amount":"42.50","currency":"EUR","direction":"debit"}]}'
    )
    with patch("doktok_provider_openai.client.httpx.post", return_value=_resp(content)):
        rows = OpenAiRecordExtractor("gpt-4o-mini", "k").extract("statement")
    assert len(rows) == 1 and rows[0].merchant == "Block House" and rows[0].amount == "42.50"
