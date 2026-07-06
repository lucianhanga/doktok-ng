"""Unit tests for the Ollama chat provider (httpx mocked; no server needed)."""

from __future__ import annotations

from typing import Any

from doktok_contracts.media import AgentMessage
from doktok_provider_ollama import OllamaChatModelProvider


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]:
        return {"response": "ok"}

    @property
    def sent(self) -> dict[str, Any]:
        return self._payload


def _capture(monkeypatch: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse(json)

    monkeypatch.setattr("doktok_provider_ollama.chat.httpx.post", fake_post)
    return captured


def test_num_ctx_is_sent_when_set(monkeypatch: Any) -> None:
    captured = _capture(monkeypatch)
    provider = OllamaChatModelProvider("qwen", "http://localhost:11434", num_ctx=32768)
    assert provider.complete("hello") == "ok"
    assert captured["json"]["options"]["num_ctx"] == 32768
    assert captured["json"]["options"]["temperature"] == 0


def test_num_ctx_omitted_when_not_set(monkeypatch: Any) -> None:
    captured = _capture(monkeypatch)
    OllamaChatModelProvider("qwen", "http://localhost:11434").complete("hello")
    assert "num_ctx" not in captured["json"]["options"]


class _FakeStream:
    def __init__(self, captured: dict[str, Any], json: dict[str, Any]) -> None:
        captured["json"] = json

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *args: Any) -> None: ...

    def raise_for_status(self) -> None: ...

    def iter_lines(self) -> list[str]:
        return ['{"message": {"content": "ok"}}']


def _capture_stream(monkeypatch: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_stream(method: str, url: str, *, json: dict[str, Any], timeout: float) -> _FakeStream:
        return _FakeStream(captured, json)

    monkeypatch.setattr("doktok_provider_ollama.chat.httpx.stream", fake_stream)
    return captured


def test_stream_uses_configured_think_when_no_override(monkeypatch: Any) -> None:
    # think=None (default) must fall back to the settings-derived self._think, not hardcode False.
    captured = _capture_stream(monkeypatch)
    provider = OllamaChatModelProvider("qwen", "http://localhost:11434", think=True)
    list(provider.stream_complete("hello"))
    assert captured["json"]["think"] is True


def test_stream_think_override_wins(monkeypatch: Any) -> None:
    # An explicit per-call think overrides the configured default (the 'Show reasoning' toggle).
    captured = _capture_stream(monkeypatch)
    provider = OllamaChatModelProvider("qwen", "http://localhost:11434", think=False)
    list(provider.stream_complete("hello", think=True))
    assert captured["json"]["think"] is True


class _UsageResponse:
    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]:
        return {
            "response": "the answer",
            "prompt_eval_count": 120,
            "eval_count": 30,
            "eval_duration": 2_000_000_000,  # 2s in ns
        }


def test_complete_records_usage(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "doktok_provider_ollama.chat.httpx.post",
        lambda url, *, json, timeout: _UsageResponse(),
    )
    provider = OllamaChatModelProvider("qwen", "http://localhost:11434")
    provider.complete("hi")
    usage = provider.get_last_usage()
    assert usage is not None
    assert usage.prompt_tokens == 120
    assert usage.answer_tokens == 30
    assert usage.reasoning_tokens == 0
    assert usage.eval_ms == 2000
    assert usage.estimated is False


class _UsageStream:
    def __enter__(self) -> _UsageStream:
        return self

    def __exit__(self, *args: Any) -> None: ...

    def raise_for_status(self) -> None: ...

    def iter_lines(self) -> list[str]:
        # ~equal reasoning/answer chars -> ~half of eval_count each.
        return [
            '{"message": {"thinking": "think think think think"}}',
            '{"message": {"content": "answer answer answer ans"}}',
            '{"done": true, "prompt_eval_count": 50, "eval_count": 40,'
            ' "eval_duration": 1000000000}',
        ]


def test_stream_records_split_usage(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "doktok_provider_ollama.chat.httpx.stream",
        lambda method, url, *, json, timeout: _UsageStream(),
    )
    provider = OllamaChatModelProvider("qwen", "http://localhost:11434", think=True)
    chunks = list(provider.stream_complete("hi"))
    assert [c.kind for c in chunks] == ["reasoning", "answer"]
    usage = provider.get_last_usage()
    assert usage is not None
    assert usage.prompt_tokens == 50
    # eval_count (40) is split by output-char ratio and always sums back to 40.
    assert usage.reasoning_tokens + usage.answer_tokens == 40
    assert usage.reasoning_tokens > 0 and usage.answer_tokens > 0
    assert usage.eval_ms == 1000
    assert usage.estimated is False


class _ToolResponse:
    def __init__(self, message: dict[str, Any]) -> None:
        self._message = message

    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]:
        return {"message": self._message}


def test_chat_with_tools_parses_tool_calls(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _ToolResponse:
        captured["json"] = json
        return _ToolResponse(
            {
                "tool_calls": [
                    {"function": {"name": "count_documents", "arguments": {"entity": "x"}}}
                ]
            }
        )

    monkeypatch.setattr("doktok_provider_ollama.chat.httpx.post", fake_post)
    provider = OllamaChatModelProvider("qwen", "http://localhost:11434")
    turn = provider.chat_with_tools(
        [AgentMessage(role="user", content="how many x")],
        [{"name": "count_documents", "description": "d", "parameters": {}}],
    )
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "count_documents"
    assert turn.tool_calls[0].arguments == {"entity": "x"}
    assert turn.text == ""
    # think is forced off for the tool loop; tools are forwarded
    assert captured["json"]["think"] is False
    assert captured["json"]["tools"][0]["function"]["name"] == "count_documents"


def test_chat_with_tools_final_text(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "doktok_provider_ollama.chat.httpx.post",
        lambda url, *, json, timeout: _ToolResponse({"content": "the answer"}),
    )
    provider = OllamaChatModelProvider("qwen", "http://localhost:11434")
    turn = provider.chat_with_tools([AgentMessage(role="user", content="hi")], [])
    assert turn.text == "the answer" and not turn.tool_calls


# ---------------------------------------------------------------------------
# stream_reply tests (issue #485)
# ---------------------------------------------------------------------------


class _MultiMessageStream:
    """Fake httpx stream that records the sent payload and returns NDJSON lines."""

    def __init__(self, captured: dict[str, Any], lines: list[str]) -> None:
        self._captured = captured
        self._lines = lines

    def __enter__(self) -> _MultiMessageStream:
        return self

    def __exit__(self, *args: Any) -> None: ...

    def raise_for_status(self) -> None: ...

    def iter_lines(self) -> list[str]:
        return self._lines


def _capture_stream_reply(monkeypatch: Any, lines: list[str]) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_stream(
        method: str, url: str, *, json: dict[str, Any], timeout: float
    ) -> _MultiMessageStream:
        captured["url"] = url
        captured["json"] = json
        return _MultiMessageStream(captured, lines)

    monkeypatch.setattr("doktok_provider_ollama.chat.httpx.stream", fake_stream)
    return captured


def test_stream_reply_sends_full_messages_list(monkeypatch: Any) -> None:
    # stream_reply must POST the full messages list to /api/chat, not a single user prompt.
    captured = _capture_stream_reply(monkeypatch, ['{"message": {"content": "ok"}}'])
    messages = [
        AgentMessage(role="system", content="You are DokTok."),
        AgentMessage(role="user", content="how many?"),
        AgentMessage(role="assistant", content="", tool_calls=[]),
        AgentMessage(
            role="tool", content="57 documents.", tool_call_id="1", name="count_documents"
        ),
    ]
    provider = OllamaChatModelProvider("qwen", "http://localhost:11434")
    list(provider.stream_reply(messages))

    sent = captured["json"]["messages"]
    # All four messages must be present in the payload.
    assert len(sent) == 4
    assert sent[0]["role"] == "system"
    assert sent[1]["role"] == "user"
    assert sent[3]["role"] == "tool"
    # The Ollama endpoint must be /api/chat (same as stream_complete).
    assert captured["url"].endswith("/api/chat")
    assert captured["json"]["stream"] is True


def test_stream_reply_yields_reasoning_and_answer_chunks(monkeypatch: Any) -> None:
    lines = [
        '{"message": {"thinking": "let me think"}}',
        '{"message": {"content": "the answer"}}',
        '{"done": true, "prompt_eval_count": 10, "eval_count": 20, "eval_duration": 500000000}',
    ]
    captured = _capture_stream_reply(monkeypatch, lines)
    _ = captured
    provider = OllamaChatModelProvider("qwen", "http://localhost:11434", think=True)
    chunks = list(provider.stream_reply([AgentMessage(role="user", content="hi")]))

    assert [c.kind for c in chunks] == ["reasoning", "answer"]
    assert chunks[0].text == "let me think"
    assert chunks[1].text == "the answer"

    # Usage is recorded after the stream.
    usage = provider.get_last_usage()
    assert usage is not None
    assert usage.prompt_tokens == 10
    assert usage.reasoning_tokens + usage.answer_tokens == 20
    assert usage.eval_ms == 500


def test_stream_reply_uses_configured_think_when_no_override(monkeypatch: Any) -> None:
    captured = _capture_stream_reply(monkeypatch, ['{"message": {"content": "ok"}}'])
    provider = OllamaChatModelProvider("qwen", "http://localhost:11434", think=True)
    list(provider.stream_reply([AgentMessage(role="user", content="hi")]))
    assert captured["json"]["think"] is True


def test_stream_reply_think_override_wins(monkeypatch: Any) -> None:
    captured = _capture_stream_reply(monkeypatch, ['{"message": {"content": "ok"}}'])
    provider = OllamaChatModelProvider("qwen", "http://localhost:11434", think=False)
    list(provider.stream_reply([AgentMessage(role="user", content="hi")], think=True))
    assert captured["json"]["think"] is True
