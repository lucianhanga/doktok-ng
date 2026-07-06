"""OpenAI adapter tests (httpx mocked - no network)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from unittest.mock import patch

import httpx
import pytest
from doktok_contracts.media import ChatChunk
from doktok_contracts.schemas import EntityType
from doktok_provider_openai import (
    OpenAiAuthError,
    OpenAiCategoryClassifier,
    OpenAiChatModelProvider,
    OpenAiEntityNerExtractor,
    OpenAiMetadataExtractor,
    OpenAiRateLimitError,
    OpenAiRecordExtractor,
    OpenAiRelationExtractor,
    OpenAiServerError,
)

# ---------------------------------------------------------------------------
# Helpers for streaming (Responses API) tests
# ---------------------------------------------------------------------------


class _FakeStreamResp:
    """Minimal httpx streaming-response stand-in for Responses API SSE tests."""

    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self.status_code = status_code
        self._lines = lines

    def __enter__(self) -> _FakeStreamResp:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def iter_lines(self) -> Iterator[str]:
        yield from self._lines


def _resp(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )


def _tool_resp(message: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": message}]},
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


def test_global_semaphore_caps_concurrent_requests() -> None:
    # All OpenAI callers share one process-wide ceiling (DOKTOK_OPENAI_MAX_CONCURRENCY) so a wide
    # reconciler fan-out + the OCR judge can't stampede the API into 429s. Cap=2, fire 6 threads,
    # assert no more than 2 requests are ever in flight at once.
    in_flight = 0
    max_seen = 0
    lock = threading.Lock()

    def fake_post(*_a: object, **_k: object) -> httpx.Response:
        nonlocal in_flight, max_seen
        with lock:
            in_flight += 1
            max_seen = max(max_seen, in_flight)
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        return _resp("ok")

    with (
        patch("doktok_provider_openai.client._REQUEST_SEMAPHORE", threading.BoundedSemaphore(2)),
        patch("doktok_provider_openai.client.httpx.post", side_effect=fake_post),
    ):
        threads = [
            threading.Thread(
                target=lambda: OpenAiChatModelProvider("gpt-4o-mini", "k").complete("hi")
            )
            for _ in range(6)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert max_seen == 2  # exactly the cap was reached, never exceeded


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


def test_chat_with_tools_parses_tool_calls() -> None:
    from doktok_contracts.media import AgentMessage

    message: dict[str, object] = {
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "count_documents", "arguments": '{"entity": "m-net"}'},
            }
        ],
    }
    with patch(
        "doktok_provider_openai.client.httpx.post", return_value=_tool_resp(message)
    ) as post:
        turn = OpenAiChatModelProvider("gpt-4o-mini", "k").chat_with_tools(
            [AgentMessage(role="user", content="how many m-net invoices")],
            [{"name": "count_documents", "description": "d", "parameters": {}}],
        )
    assert len(turn.tool_calls) == 1
    call = turn.tool_calls[0]
    assert call.name == "count_documents" and call.arguments == {"entity": "m-net"}
    assert turn.text == ""
    # the tools are forwarded as OpenAI function specs
    body = post.call_args.kwargs["json"]
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "count_documents"


def test_chat_with_tools_final_text() -> None:
    from doktok_contracts.media import AgentMessage

    with patch(
        "doktok_provider_openai.client.httpx.post", return_value=_tool_resp({"content": "done"})
    ):
        turn = OpenAiChatModelProvider("gpt-4o-mini", "k").chat_with_tools(
            [AgentMessage(role="user", content="hi")], []
        )
    assert turn.text == "done" and not turn.tool_calls


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


def test_record_repairs_invalid_json() -> None:
    broken = '{"transactions":[{"date":"2024-02-03","merchant":"Block Hou'  # truncated
    good = '{"transactions":[{"merchant":"Block House","amount":"42.50","currency":"EUR"}]}'
    with patch(
        "doktok_provider_openai.client.httpx.post",
        side_effect=[_resp(broken), _resp(good)],
    ) as post:
        rows = OpenAiRecordExtractor("gpt-4o-mini", "k").extract("statement")
    assert len(rows) == 1 and rows[0].merchant == "Block House"
    assert post.call_count == 2  # second call is the repair pass


def test_record_raises_after_failed_repair() -> None:
    with (
        patch(
            "doktok_provider_openai.client.httpx.post",
            side_effect=[_resp("not json"), _resp("still not json")],
        ),
        pytest.raises(RuntimeError, match="after repair"),
    ):
        OpenAiRecordExtractor("gpt-4o-mini", "k").extract("statement")


def test_relation_repairs_invalid_json() -> None:
    broken = '{"triples":[{"subject":"Acme","predicate":"issued_b'  # truncated
    good = (
        '{"triples":[{"subject":"Acme","predicate":"issued_by","object":"Bob",'
        '"subject_type":"ORG","object_type":"PERSON","evidence":"Acme issued by Bob"}]}'
    )
    with patch(
        "doktok_provider_openai.client.httpx.post",
        side_effect=[_resp(broken), _resp(good)],
    ) as post:
        rels = OpenAiRelationExtractor("gpt-4o-mini", "k").extract(
            "text", [("Acme", "ORG"), ("Bob", "PERSON")]
        )
    assert len(rels) == 1 and rels[0].subject == "Acme" and rels[0].object == "Bob"
    assert post.call_count == 2  # second call is the repair pass


def test_classify_repairs_invalid_json() -> None:
    broken = '{"categories":["Invoice","Med'  # truncated
    good = '{"categories":["Invoice","Medical"]}'
    with patch(
        "doktok_provider_openai.client.httpx.post",
        side_effect=[_resp(broken), _resp(good)],
    ) as post:
        labels = OpenAiCategoryClassifier("gpt-4o-mini", "k").classify("text", [])
    assert labels == ["Invoice", "Medical"]
    assert post.call_count == 2  # second call is the repair pass


def test_metadata_repairs_invalid_json() -> None:
    broken = '{"title":"Invoice","document_date":"2024-02-0'  # truncated
    good = (
        '{"title":"Invoice","document_date":"2024-02-03",'
        '"document_location":"Berlin","summary":"S"}'
    )
    with patch(
        "doktok_provider_openai.client.httpx.post",
        side_effect=[_resp(broken), _resp(good)],
    ) as post:
        md = OpenAiMetadataExtractor("gpt-4o-mini", "k").extract("body text")
    assert md.title == "Invoice" and md.location == "Berlin"
    assert post.call_count == 2  # second call is the repair pass


# ---------------------------------------------------------------------------
# Streaming (Responses API) tests
# ---------------------------------------------------------------------------


def test_stream_complete_yields_reasoning_then_answer_chunks() -> None:
    """Responses API SSE: reasoning-summary deltas come before answer deltas as separate chunks."""
    sse_lines = [
        'data: {"type": "response.reasoning_summary_text.delta", "delta": "thinking..."}',
        'data: {"type": "response.reasoning_summary_text.delta", "delta": " more"}',
        'data: {"type": "response.output_text.delta", "delta": "Hello"}',
        'data: {"type": "response.output_text.delta", "delta": " world"}',
        (
            'data: {"type": "response.completed", "response": {"usage": {'
            '"input_tokens": 5, "output_tokens": 10, '
            '"output_tokens_details": {"reasoning_tokens": 3}}}}'
        ),
    ]
    fake = _FakeStreamResp(sse_lines)
    with patch("doktok_provider_openai.client.httpx.stream", return_value=fake):
        chunks = list(
            OpenAiChatModelProvider("gpt-5", "k", reasoning_effort="medium").stream_complete("hi")
        )
    assert chunks == [
        ChatChunk(kind="reasoning", text="thinking..."),
        ChatChunk(kind="reasoning", text=" more"),
        ChatChunk(kind="answer", text="Hello"),
        ChatChunk(kind="answer", text=" world"),
    ]


def test_stream_complete_captures_usage_from_completed_event() -> None:
    """stream_complete updates _last_usage from the response.completed usage block."""
    sse_lines = [
        'data: {"type": "response.output_text.delta", "delta": "Hi"}',
        (
            'data: {"type": "response.completed", "response": {"usage": {'
            '"input_tokens": 8, "output_tokens": 12, '
            '"output_tokens_details": {"reasoning_tokens": 4}}}}'
        ),
    ]
    fake = _FakeStreamResp(sse_lines)
    provider = OpenAiChatModelProvider("gpt-5", "k", reasoning_effort="low")
    with patch("doktok_provider_openai.client.httpx.stream", return_value=fake):
        list(provider.stream_complete("hi"))
    usage = provider.get_last_usage()
    assert usage is not None
    assert usage.prompt_tokens == 8
    assert usage.reasoning_tokens == 4
    assert usage.answer_tokens == 8  # output_tokens(12) - reasoning_tokens(4)
    assert not usage.estimated


def test_stream_complete_fallback_when_responses_api_unavailable() -> None:
    """When the Responses API returns 4xx, stream_complete falls back to the non-streaming path."""
    fake_404 = _FakeStreamResp([], status_code=404)
    with (
        patch("doktok_provider_openai.client.httpx.stream", return_value=fake_404),
        patch("doktok_provider_openai.client.httpx.post", return_value=_resp("fallback answer")),
    ):
        chunks = list(OpenAiChatModelProvider("gpt-4o-mini", "k").stream_complete("hi"))
    assert chunks == [ChatChunk(kind="answer", text="fallback answer")]


def test_stream_complete_fallback_on_network_error() -> None:
    """When the Responses API raises a network error, stream_complete falls back gracefully."""
    with (
        patch(
            "doktok_provider_openai.client.httpx.stream",
            side_effect=httpx.ConnectError("connection refused"),
        ),
        patch("doktok_provider_openai.client.httpx.post", return_value=_resp("fallback answer")),
    ):
        chunks = list(OpenAiChatModelProvider("gpt-4o-mini", "k").stream_complete("hi"))
    assert chunks == [ChatChunk(kind="answer", text="fallback answer")]


def test_stream_complete_ignores_unknown_event_types() -> None:
    """Unknown SSE event types are silently skipped; known deltas still yield chunks."""
    sse_lines = [
        'data: {"type": "response.created", "response": {}}',
        'data: {"type": "response.in_progress", "response": {}}',
        'data: {"type": "response.output_text.delta", "delta": "answer"}',
        'data: {"type": "response.completed", "response": {}}',
    ]
    fake = _FakeStreamResp(sse_lines)
    with patch("doktok_provider_openai.client.httpx.stream", return_value=fake):
        chunks = list(OpenAiChatModelProvider("gpt-4o-mini", "k").stream_complete("hi"))
    assert chunks == [ChatChunk(kind="answer", text="answer")]


def test_stream_complete_no_reasoning_payload_when_effort_absent() -> None:
    """Without reasoning_effort the Responses API payload must not include a reasoning key."""
    sse_lines = [
        'data: {"type": "response.output_text.delta", "delta": "ok"}',
        'data: {"type": "response.completed", "response": {}}',
    ]
    fake = _FakeStreamResp(sse_lines)
    with patch("doktok_provider_openai.client.httpx.stream", return_value=fake) as mock_stream:
        list(OpenAiChatModelProvider("gpt-4o-mini", "k").stream_complete("hi"))
    payload = mock_stream.call_args.kwargs["json"]
    assert "reasoning" not in payload
    assert payload["stream"] is True


def test_stream_complete_reasoning_payload_when_effort_set() -> None:
    """With reasoning_effort the payload must include reasoning.effort and summary=auto."""
    sse_lines = [
        'data: {"type": "response.output_text.delta", "delta": "ok"}',
        'data: {"type": "response.completed", "response": {}}',
    ]
    fake = _FakeStreamResp(sse_lines)
    with patch("doktok_provider_openai.client.httpx.stream", return_value=fake) as mock_stream:
        list(OpenAiChatModelProvider("gpt-5", "k", reasoning_effort="high").stream_complete("hi"))
    payload = mock_stream.call_args.kwargs["json"]
    assert payload["reasoning"] == {"effort": "high", "summary": "auto"}


# ---------------------------------------------------------------------------
# stream_reply tests (issue #485)
# ---------------------------------------------------------------------------


def test_stream_reply_builds_responses_input_from_messages() -> None:
    """stream_reply must POST the full message list as Responses API input items."""
    from doktok_contracts.media import AgentMessage, LlmToolCall

    sse_lines = [
        'data: {"type": "response.output_text.delta", "delta": "57 docs [1]."}',
        'data: {"type": "response.completed", "response": {}}',
    ]
    fake = _FakeStreamResp(sse_lines)
    messages = [
        AgentMessage(role="system", content="You are DokTok."),
        AgentMessage(role="user", content="how many?"),
        AgentMessage(
            role="assistant",
            content="",
            tool_calls=[
                LlmToolCall(id="call_1", name="count_documents", arguments={"entity": "x"})
            ],
        ),
        AgentMessage(
            role="tool", content="57 documents.", tool_call_id="call_1", name="count_documents"
        ),
    ]
    with patch("doktok_provider_openai.client.httpx.stream", return_value=fake) as mock_stream:
        chunks = list(OpenAiChatModelProvider("gpt-4o-mini", "k").stream_reply(messages))

    payload = mock_stream.call_args.kwargs["json"]
    # System message becomes instructions, not an input item.
    assert payload["instructions"] == "You are DokTok."
    input_items = payload["input"]
    # user message, function_call item, function_call_output item (system excluded)
    assert len(input_items) == 3
    assert input_items[0] == {"role": "user", "content": "how many?"}
    assert input_items[1]["type"] == "function_call"
    assert input_items[1]["call_id"] == "call_1"
    assert input_items[1]["name"] == "count_documents"
    assert input_items[2]["type"] == "function_call_output"
    assert input_items[2]["call_id"] == "call_1"
    assert input_items[2]["output"] == "57 documents."
    # Verify the answer chunk was emitted
    assert chunks == [ChatChunk(kind="answer", text="57 docs [1].")]


def test_stream_reply_yields_reasoning_and_answer_chunks() -> None:
    """stream_reply yields reasoning and answer chunks identically to stream_complete."""
    from doktok_contracts.media import AgentMessage

    sse_lines = [
        'data: {"type": "response.reasoning_summary_text.delta", "delta": "thinking..."}',
        'data: {"type": "response.output_text.delta", "delta": "the answer [1]."}',
        (
            'data: {"type": "response.completed", "response": {"usage": {'
            '"input_tokens": 20, "output_tokens": 8, '
            '"output_tokens_details": {"reasoning_tokens": 3}}}}'
        ),
    ]
    fake = _FakeStreamResp(sse_lines)
    provider = OpenAiChatModelProvider("gpt-5", "k", reasoning_effort="medium")
    with patch("doktok_provider_openai.client.httpx.stream", return_value=fake):
        chunks = list(provider.stream_reply([AgentMessage(role="user", content="q")]))
    assert chunks == [
        ChatChunk(kind="reasoning", text="thinking..."),
        ChatChunk(kind="answer", text="the answer [1]."),
    ]
    usage = provider.get_last_usage()
    assert usage is not None
    assert usage.prompt_tokens == 20
    assert usage.reasoning_tokens == 3
    assert usage.answer_tokens == 5  # 8 output - 3 reasoning


def test_stream_reply_fallback_when_responses_api_unavailable() -> None:
    """When the Responses API returns 4xx, stream_reply falls back to complete()."""
    from doktok_contracts.media import AgentMessage

    fake_404 = _FakeStreamResp([], status_code=404)
    messages = [
        AgentMessage(role="user", content="what is this?"),
    ]
    with (
        patch("doktok_provider_openai.client.httpx.stream", return_value=fake_404),
        patch("doktok_provider_openai.client.httpx.post", return_value=_resp("fallback answer")),
    ):
        chunks = list(OpenAiChatModelProvider("gpt-4o-mini", "k").stream_reply(messages))
    assert chunks == [ChatChunk(kind="answer", text="fallback answer")]


def test_to_responses_input_items_mapping() -> None:
    """_to_responses_input_items correctly maps all AgentMessage roles."""
    import json as json_mod

    from doktok_contracts.media import AgentMessage, LlmToolCall
    from doktok_provider_openai.client import _to_responses_input_items

    messages = [
        AgentMessage(role="system", content="sys prompt"),
        AgentMessage(role="user", content="question"),
        AgentMessage(
            role="assistant",
            content="thinking",
            tool_calls=[LlmToolCall(id="c1", name="fn", arguments={"k": "v"})],
        ),
        AgentMessage(role="tool", content="result", tool_call_id="c1", name="fn"),
        AgentMessage(role="assistant", content="final"),
    ]
    instructions, items = _to_responses_input_items(messages)

    assert instructions == "sys prompt"
    # assistant with tool_calls emits function_call item + its text content
    # user, function_call (from assistant), function_call_output (tool), assistant text
    assert len(items) == 5
    assert items[0] == {"role": "user", "content": "question"}
    assert items[1]["type"] == "function_call" and items[1]["call_id"] == "c1"
    assert json_mod.loads(items[1]["arguments"]) == {"k": "v"}
    assert items[2] == {"role": "assistant", "content": "thinking"}
    assert items[3]["type"] == "function_call_output" and items[3]["output"] == "result"
    assert items[4] == {"role": "assistant", "content": "final"}
