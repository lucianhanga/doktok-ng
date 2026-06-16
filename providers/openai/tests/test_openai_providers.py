"""OpenAI adapter tests (httpx mocked - no network)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from doktok_contracts.schemas import EntityType
from doktok_provider_openai import (
    OpenAiAuthError,
    OpenAiChatModelProvider,
    OpenAiEntityNerExtractor,
    OpenAiMetadataExtractor,
    OpenAiRateLimitError,
    OpenAiRecordExtractor,
    OpenAiServerError,
)


def _resp(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )


def _status(code: int, *, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        code,
        json={"error": {"message": f"http {code}"}},
        headers=headers or {},
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )


def test_retries_429_then_succeeds() -> None:
    # 429 (with Retry-After) then 200 -> one retry, eventual success. Sleep is patched out.
    with (
        patch("doktok_provider_openai.client.time.sleep") as sleep,
        patch(
            "doktok_provider_openai.client.httpx.post",
            side_effect=[_status(429, headers={"Retry-After": "0"}), _resp("ok")],
        ) as post,
    ):
        out = OpenAiChatModelProvider("gpt-4o-mini", "k").complete("hi")
    assert out == "ok"
    assert post.call_count == 2 and sleep.call_count == 1


def test_auth_error_fails_fast_without_retry() -> None:
    with (
        patch("doktok_provider_openai.client.time.sleep") as sleep,
        patch("doktok_provider_openai.client.httpx.post", return_value=_status(401)) as post,
        pytest.raises(OpenAiAuthError),
    ):
        OpenAiChatModelProvider("gpt-4o-mini", "bad-key").complete("hi")
    assert post.call_count == 1 and sleep.call_count == 0  # no retries on auth failure


def test_server_error_retries_then_raises_classified() -> None:
    with (
        patch("doktok_provider_openai.client.time.sleep"),
        patch("doktok_provider_openai.client.httpx.post", return_value=_status(503)) as post,
        pytest.raises(OpenAiServerError),
    ):
        OpenAiChatModelProvider("gpt-4o-mini", "k").complete("hi")
    assert post.call_count == 4  # initial + 3 retries


def test_persistent_429_raises_rate_limit_error() -> None:
    with (
        patch("doktok_provider_openai.client.time.sleep"),
        patch("doktok_provider_openai.client.httpx.post", return_value=_status(429)),
        pytest.raises(OpenAiRateLimitError),
    ):
        OpenAiChatModelProvider("gpt-4o-mini", "k").complete("hi")


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


def test_ner_caps_output_and_extracts_types() -> None:
    content = '{"people":["Bob"],"organizations":["IBM"],"places":["Berlin"]}'
    with patch("doktok_provider_openai.client.httpx.post", return_value=_resp(content)) as post:
        out = OpenAiEntityNerExtractor("gpt-4o-mini", "k").extract("text")
    pairs = {(e.entity_type, e.entity_text) for e in out}
    assert pairs == {
        (EntityType.PERSON, "Bob"),
        (EntityType.ORG, "IBM"),
        (EntityType.GPE, "Berlin"),
    }
    # The schema bounds each array so dense documents can't overrun the output and truncate.
    schema = post.call_args.kwargs["json"]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["people"]["maxItems"] == 60


def test_ner_repairs_truncated_json() -> None:
    broken = '{"people":["Angela Merkel","Olaf Sc'  # truncated mid-name
    good = '{"people":["Angela Merkel"],"organizations":[],"places":[]}'
    with patch(
        "doktok_provider_openai.client.httpx.post",
        side_effect=[_resp(broken), _resp(good)],
    ) as post:
        out = OpenAiEntityNerExtractor("gpt-4o-mini", "k").extract("text")
    assert [(e.entity_type, e.entity_text) for e in out] == [(EntityType.PERSON, "Angela Merkel")]
    assert post.call_count == 2  # second call is the repair pass
