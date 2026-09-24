"""Unit-тесты GeminiProvider (raw httpx + SSE) и build_generate_content_payload."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.llm.base import LLMRequest, LLMTool
from app.llm.errors import (
    AuthError,
    ForbiddenError,
    InvalidRequestError,
    ProviderError,
    RateLimitError,
    SafetyError,
    ServerError,
)
from app.llm.events import Done, ReasoningDelta, TextDelta, ToolCall, Usage
from app.llm.providers.gemini import (
    GeminiProvider,
    build_generate_content_payload,
)


def _sse(*chunks: dict[str, Any] | str) -> bytes:
    """Собрать SSE-поток: каждый чанк — строка "data: {json}\\n\\n"."""
    frames = []
    for chunk in chunks:
        data = chunk if isinstance(chunk, str) else json.dumps(chunk)
        frames.append(f"data: {data}")
    return ("\n\n".join(frames) + "\n\n").encode()


def _provider_for(content: bytes, status: int = 200) -> GeminiProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, content=content, headers={"content-type": "text/event-stream"}
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://gemini.test"
    )
    return GeminiProvider(base_url="https://gemini.test", http_client=client)


def _request(**kw: Any) -> LLMRequest:
    kw.setdefault("metadata", {"api_key": "test-key"})
    kw.setdefault("messages", [{"role": "user", "parts": [{"type": "text", "text": "hi"}]}])
    return LLMRequest(model="gemini-3.8-flash", **kw)


async def _collect(provider: GeminiProvider, request: LLMRequest) -> list[Any]:
    return [event async for event in provider.stream_chat(request)]


async def test_text_stream() -> None:
    body = _sse(
        {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": " world"}]}, "finishReason": "STOP"}]},
    )
    events = await _collect(_provider_for(body), _request())
    assert events == [TextDelta("Hello"), TextDelta(" world"), Done("stop")]


async def test_reasoning_hidden_as_reasoning_delta() -> None:
    body = _sse(
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": "hmm", "thought": True}, {"text": "answer"}]},
                    "finishReason": "STOP",
                }
            ]
        }
    )
    events = await _collect(_provider_for(body), _request())
    assert events == [ReasoningDelta("hmm"), TextDelta("answer"), Done("stop")]


async def test_function_call_with_thought_signature() -> None:
    body = _sse(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {"name": "web_search", "args": {"q": "x"}},
                                "thoughtSignature": "sig123",
                            }
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }
    )
    events = await _collect(_provider_for(body), _request())
    assert len(events) == 2
    tool_call = events[0]
    assert isinstance(tool_call, ToolCall)
    assert tool_call.name == "web_search"
    assert tool_call.arguments_json == json.dumps({"q": "x"})
    assert tool_call.provider_meta["thought_signature"] == "sig123"
    assert tool_call.id
    assert events[1] == Done("stop")


async def test_usage_metadata() -> None:
    body = _sse(
        {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "thoughtsTokenCount": 2,
                "totalTokenCount": 17,
            },
        }
    )
    events = await _collect(_provider_for(body), _request())
    usage = next(e for e in events if isinstance(e, Usage))
    assert usage == Usage(input_tokens=10, output_tokens=5, reasoning_tokens=2, total_tokens=17)


async def test_finish_reason_max_tokens_maps_to_length() -> None:
    body = _sse(
        {"candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": "MAX_TOKENS"}]}
    )
    events = await _collect(_provider_for(body), _request())
    assert events[-1] == Done("length")


async def test_finish_reason_safety_raises() -> None:
    body = _sse({"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]})
    with pytest.raises(SafetyError):
        await _collect(_provider_for(body), _request())


async def test_prompt_feedback_block_reason_raises() -> None:
    body = _sse({"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(SafetyError):
        await _collect(_provider_for(body), _request())


async def test_missing_api_key_raises() -> None:
    provider = _provider_for(_sse())
    with pytest.raises(InvalidRequestError):
        await _collect(provider, _request(metadata={}))


async def test_cancellation_stops_without_done() -> None:
    body = _sse(
        {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": "STOP"}]},
    )
    cancellation = asyncio.Event()
    cancellation.set()
    events = await _collect(_provider_for(body), _request(cancellation=cancellation))
    assert events == []


async def test_empty_text_part_with_signature_is_skipped() -> None:
    body = _sse(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "real"},
                            {"text": "", "thoughtSignature": "sig-only"},
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }
    )
    events = await _collect(_provider_for(body), _request())
    assert events == [TextDelta("real"), Done("stop")]


async def test_done_sentinel_stops_stream() -> None:
    body = _sse(
        {"candidates": [{"content": {"parts": [{"text": "a"}]}}]},
        "[DONE]",
        {"candidates": [{"content": {"parts": [{"text": "b"}]}}]},
    )
    events = await _collect(_provider_for(body), _request())
    assert events == [TextDelta("a")]


def test_payload_system_prompt_and_contents() -> None:
    payload = build_generate_content_payload(_request(system_prompt="be nice"))
    assert payload["systemInstruction"] == {"parts": [{"text": "be nice"}]}
    assert payload["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]


def test_payload_image_part() -> None:
    request = _request(
        messages=[
            {
                "role": "user",
                "parts": [
                    {"type": "text", "text": "what is"},
                    {"type": "text", "text": "this"},
                    {"type": "image", "mime_type": "image/png", "data_base64": "QUJD"},
                ],
            }
        ]
    )
    payload = build_generate_content_payload(request)
    assert payload["contents"][0]["parts"] == [
        {"text": "what is\nthis"},
        {"inlineData": {"mimeType": "image/png", "data": "QUJD"}},
    ]


def test_payload_assistant_tool_call_returns_thought_signature() -> None:
    request = _request(
        messages=[
            {
                "role": "assistant",
                "parts": [
                    {"type": "text", "text": "let me check"},
                    {
                        "type": "tool_call",
                        "id": "call_1",
                        "name": "web_search",
                        "arguments_json": '{"q": "x"}',
                        "provider_meta": {"thought_signature": "sig123"},
                    },
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool_result",
                        "call_id": "call_1",
                        "name": "web_search",
                        "content": "42",
                        "is_error": False,
                    }
                ],
            },
        ]
    )
    payload = build_generate_content_payload(request)
    assert payload["contents"][0] == {
        "role": "model",
        "parts": [
            {"text": "let me check"},
            {
                "functionCall": {"name": "web_search", "args": {"q": "x"}},
                "thoughtSignature": "sig123",
            },
        ],
    }
    assert payload["contents"][1] == {
        "role": "user",
        "parts": [{"functionResponse": {"name": "web_search", "response": {"result": "42"}}}],
    }


def test_payload_thinking_level_and_max_output_tokens() -> None:
    payload = build_generate_content_payload(_request(thinking="HIGH", max_output_tokens=1024))
    generation_config = payload["generationConfig"]
    assert generation_config["thinkingConfig"] == {"thinkingLevel": "high"}
    assert generation_config["maxOutputTokens"] == 1024
    assert "temperature" not in generation_config
    assert "topP" not in generation_config
    assert "topK" not in generation_config


def test_payload_no_generation_config_when_empty() -> None:
    payload = build_generate_content_payload(_request())
    assert "generationConfig" not in payload


def test_payload_tools_and_tool_choice() -> None:
    tools = [LLMTool(name="web_search", description="search", parameters={"type": "object"})]
    payload = build_generate_content_payload(_request(tools=tools, tool_choice="none"))
    assert payload["tools"] == [
        {
            "functionDeclarations": [
                {"name": "web_search", "description": "search", "parameters": {"type": "object"}}
            ]
        }
    ]
    assert payload["toolConfig"] == {"functionCallingConfig": {"mode": "NONE"}}

    payload_auto = build_generate_content_payload(_request(tools=tools, tool_choice="auto"))
    assert payload_auto["toolConfig"] == {"functionCallingConfig": {"mode": "AUTO"}}

    payload_default = build_generate_content_payload(_request(tools=tools))
    assert "toolConfig" not in payload_default


@pytest.mark.parametrize(
    ("status", "exc_class"),
    [
        (400, InvalidRequestError),
        (401, AuthError),
        (403, ForbiddenError),
        (404, InvalidRequestError),
        (429, RateLimitError),
        (500, ServerError),
    ],
)
async def test_http_error_mapping(status: int, exc_class: type[ProviderError]) -> None:
    body = json.dumps(
        {"error": {"code": status, "message": "boom", "status": "SOME_STATUS"}}
    ).encode()
    provider = _provider_for(body, status=status)
    with pytest.raises(exc_class) as exc_info:
        await _collect(provider, _request())
    assert exc_info.value.status_code == status
    assert exc_info.value.raw_code == "SOME_STATUS"


async def test_rate_limit_retry_after_from_retry_info() -> None:
    body = json.dumps(
        {
            "error": {
                "code": 429,
                "message": "quota",
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "4s"}
                ],
            }
        }
    ).encode()
    provider = _provider_for(body, status=429)
    with pytest.raises(RateLimitError) as exc_info:
        await _collect(provider, _request())
    assert exc_info.value.retry_after == 4.0
    assert exc_info.value.retryable is True


def test_sanitize_gemini_schema_strips_unsupported_keys() -> None:
    """additionalProperties/$schema и прочие не-Gemini ключи вырезаются рекурсивно."""
    from app.llm.providers.gemini import sanitize_gemini_schema

    schema = {
        "type": "object",
        "additionalProperties": False,
        "$schema": "http://json-schema.org/draft-07/schema#",
        "properties": {
            "query": {"type": "string", "description": "q"},
            "nested": {
                "type": "object",
                "additionalProperties": True,
                "properties": {"x": {"type": "integer", "minimum": 1}},
            },
        },
        "required": ["query"],
    }
    cleaned = sanitize_gemini_schema(schema)
    assert "additionalProperties" not in cleaned
    assert "$schema" not in cleaned
    assert cleaned["properties"]["nested"]["properties"]["x"] == {"type": "integer", "minimum": 1}
    assert "additionalProperties" not in cleaned["properties"]["nested"]
    assert cleaned["required"] == ["query"]


def test_payload_tools_are_sanitized() -> None:
    """В payload functionDeclarations не попадают неподдерживаемые ключи JSON Schema."""
    request = _request(
        tools=[
            LLMTool(
                name="web_search",
                description="search",
                parameters={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"query": {"type": "string"}},
                },
            )
        ]
    )
    payload = build_generate_content_payload(request)
    params = payload["tools"][0]["functionDeclarations"][0]["parameters"]
    assert "additionalProperties" not in params
    assert params["properties"]["query"]["type"] == "string"
