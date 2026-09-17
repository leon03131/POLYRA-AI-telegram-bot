"""Unit-тесты AlibabaProvider: wire-стрим, thinking-маппинг, ошибки, retry, cancellation."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.llm.base import LLMRequest, LLMTool
from app.llm.errors import (
    AuthError,
    InvalidRequestError,
    RateLimitError,
    ServerError,
)
from app.llm.events import Done, ReasoningDelta, TextDelta, ToolCall, Usage
from app.llm.providers.alibaba import AlibabaProvider, build_chat_completions_payload

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _make_provider(
    handler: Callable[[httpx.Request], httpx.Response],
) -> AlibabaProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE_URL)
    return AlibabaProvider(api_key="sk-test", base_url=BASE_URL, http_client=client)


def _request(model: str = "qwen3.8-flash", **kw: Any) -> LLMRequest:
    return LLMRequest(
        model=model,
        messages=[{"role": "user", "parts": [{"type": "text", "text": "hi"}]}],
        **kw,
    )


def _chunk(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> str:
    payload: dict[str, Any] = {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    payload.update(extra)
    return "data: " + json.dumps(payload)


def _sse_response(lines: list[str], status: int = 200) -> httpx.Response:
    body = ("\n".join(lines) + "\n").encode()
    return httpx.Response(status, content=body, headers={"content-type": "text/event-stream"})


def _error_response(status: int, code: str, message: str = "boom") -> httpx.Response:
    return httpx.Response(
        status,
        json={"error": {"message": message, "type": "error", "param": None, "code": code}},
    )


async def _collect(provider: AlibabaProvider, request: LLMRequest) -> list[Any]:
    return [event async for event in provider.stream_chat(request)]


async def test_text_stream() -> None:
    lines = [
        _chunk({"role": "assistant", "content": "Hello"}),
        _chunk({"content": " world"}),
        _chunk({}, finish="stop"),
        "data: [DONE]",
    ]
    provider = _make_provider(lambda req: _sse_response(lines))
    events = await _collect(provider, _request())
    assert events == [TextDelta("Hello"), TextDelta(" world"), Done("stop")]


async def test_reasoning_delta_not_text() -> None:
    lines = [
        _chunk({"reasoning_content": "let me think"}),
        _chunk({"content": "answer"}),
        _chunk({}, finish="stop"),
        "data: [DONE]",
    ]
    provider = _make_provider(lambda req: _sse_response(lines))
    events = await _collect(provider, _request())
    assert events == [ReasoningDelta("let me think"), TextDelta("answer"), Done("stop")]
    assert not any(isinstance(e, TextDelta) and e.text == "let me think" for e in events)


async def test_tool_calls_incremental() -> None:
    lines = [
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "function": {"name": "web_search", "arguments": '{"q":'},
                    }
                ]
            }
        ),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"x"}'}}]}),
        _chunk({}, finish="tool_calls"),
        "data: [DONE]",
    ]
    provider = _make_provider(lambda req: _sse_response(lines))
    events = await _collect(provider, _request())
    assert events == [
        ToolCall(id="call_1", name="web_search", arguments_json='{"q":"x"}'),
        Done("tool_calls"),
    ]


async def test_usage_final_chunk() -> None:
    lines = [
        _chunk({"content": "ok"}),
        _chunk({}, finish="stop"),
        "data: "
        + json.dumps(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
            }
        ),
        "data: [DONE]",
    ]
    provider = _make_provider(lambda req: _sse_response(lines))
    events = await _collect(provider, _request())
    assert events == [
        TextDelta("ok"),
        Done("stop"),
        Usage(input_tokens=10, output_tokens=5, reasoning_tokens=3, total_tokens=15),
    ]


@pytest.mark.parametrize(
    ("model", "thinking", "expected", "absent"),
    [
        (
            "qwen3.8-flash",
            "off",
            {"enable_thinking": False, "preserve_thinking": False},
            ["reasoning_effort", "thinking_budget"],
        ),
        (
            "qwen3.8-max",
            "max",
            {"reasoning_effort": "xhigh", "preserve_thinking": False},
            ["enable_thinking", "thinking_budget"],
        ),
        (
            "qwen3.8-flash",
            "medium",
            {"reasoning_effort": "medium", "preserve_thinking": False},
            ["enable_thinking", "thinking_budget"],
        ),
        (
            "deepseek-v4.1-flash",
            "low",
            {"reasoning_effort": "low"},
            ["enable_thinking", "preserve_thinking", "thinking_budget"],
        ),
        (
            "glm-5.3",
            "high",
            {"reasoning_effort": "high", "clear_thinking": True},
            ["enable_thinking", "preserve_thinking", "thinking_budget"],
        ),
        (
            "kimi-k3",
            "max",
            {"reasoning_effort": "max"},
            ["enable_thinking", "preserve_thinking", "thinking_budget"],
        ),
        (
            "qwen3.8-flash",
            None,
            {"preserve_thinking": False},
            ["enable_thinking", "reasoning_effort", "thinking_budget"],
        ),
        (
            "kimi-k3",
            None,
            {},
            ["enable_thinking", "reasoning_effort", "preserve_thinking", "clear_thinking"],
        ),
    ],
)
def test_build_payload_thinking(
    model: str, thinking: str | None, expected: dict[str, Any], absent: list[str]
) -> None:
    payload = build_chat_completions_payload(_request(model=model, thinking=thinking))
    for key, value in expected.items():
        assert payload.get(key) == value, f"{model}/{thinking}: {key}"
    for key in absent:
        assert key not in payload, f"{model}/{thinking}: unexpected {key}"
    assert not ("reasoning_effort" in payload and "thinking_budget" in payload)


def test_build_payload_messages_mapping() -> None:
    request = LLMRequest(
        model="qwen3.8-flash",
        system_prompt="You are helpful.",
        messages=[
            {
                "role": "user",
                "parts": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image", "mime_type": "image/png", "data_base64": "QUJD"},
                ],
            },
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool_call",
                        "id": "call_1",
                        "name": "web_search",
                        "arguments_json": '{"q":"x"}',
                        "provider_meta": {},
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool_result",
                        "call_id": "call_1",
                        "name": "web_search",
                        "content": "search result",
                        "is_error": False,
                    }
                ],
            },
        ],
        thinking="off",
    )
    payload = build_chat_completions_payload(request)
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    messages = payload["messages"]
    assert messages[0] == {"role": "system", "content": "You are helpful."}
    assert messages[1] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "what is this?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
        ],
    }
    assert messages[2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "web_search", "arguments": '{"q":"x"}'},
            }
        ],
    }
    assert messages[3] == {"role": "tool", "tool_call_id": "call_1", "content": "search result"}


def test_build_payload_tools_and_options() -> None:
    request = _request(
        tools=[LLMTool(name="web_search", description="Search", parameters={"type": "object"})],
        tool_choice="auto",
        max_output_tokens=512,
    )
    payload = build_chat_completions_payload(request)
    assert payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search",
                "parameters": {"type": "object"},
            },
        }
    ]
    assert payload["tool_choice"] == "auto"
    assert payload["max_completion_tokens"] == 512


@pytest.mark.parametrize(
    ("status", "exc_cls"),
    [
        (400, InvalidRequestError),
        (401, AuthError),
        (429, RateLimitError),
        (500, ServerError),
    ],
)
async def test_http_error_classification(status: int, exc_cls: type[Exception]) -> None:
    provider = _make_provider(lambda req: _error_response(status, "Throttling.RateQuota"))
    with pytest.raises(exc_cls) as exc_info:
        await _collect(provider, _request())
    assert exc_info.value.raw_code == "Throttling.RateQuota"
    assert exc_info.value.status_code == status


async def test_retry_once_on_500_then_success() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _error_response(500, "InternalError")
        return _sse_response([_chunk({"content": "hi"}), _chunk({}, finish="stop"), "data: [DONE]"])

    provider = _make_provider(handler)
    events = await _collect(provider, _request())
    assert calls == 2
    assert events == [TextDelta("hi"), Done("stop")]


async def test_no_retry_on_400() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _error_response(400, "InvalidParameter")

    provider = _make_provider(handler)
    with pytest.raises(InvalidRequestError):
        await _collect(provider, _request())
    assert calls == 1


async def test_stream_error_throttling_raises_rate_limit() -> None:
    lines = [
        "data: "
        + json.dumps({"error": {"code": "Throttling.RateQuota", "message": "quota exceeded"}}),
        "data: [DONE]",
    ]
    provider = _make_provider(lambda req: _sse_response(lines))
    with pytest.raises(RateLimitError):
        await _collect(provider, _request())


async def test_cancellation_before_iteration_yields_nothing() -> None:
    cancel = asyncio.Event()
    cancel.set()
    lines = [_chunk({"content": "hi"}), _chunk({}, finish="stop"), "data: [DONE]"]
    provider = _make_provider(lambda req: _sse_response(lines))
    events = await _collect(provider, _request(cancellation=cancel))
    assert events == []


async def test_default_client_trust_env_false() -> None:
    provider = AlibabaProvider(api_key="sk-test", base_url=BASE_URL)
    assert provider._client._trust_env is False
    await provider.aclose()
