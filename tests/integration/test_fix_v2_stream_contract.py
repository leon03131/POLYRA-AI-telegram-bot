"""FIX_V2 §1 stream contract — offline, provider→consumer, без сети и БД.

Полный поток: httpx.MockTransport → SSE-парсер провайдера (alibaba/gemini)
→ события LLMEvent → потребитель GenerationService._consume (фейковый
streamer). Контракт (.agents/POLYRA_FIX_V2_CONTRACTS.md §1):
- поток завершается ровно одним терминальным Done;
- Usage (если провайдер его даёт) приходит строго ДО Done;
- EOF без Done = unexpected EOF → ошибка, а не успех;
- отмена (cancellation) до/во время итерации — тихое завершение без событий.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.llm.base import LLMRequest
from app.llm.errors import NetworkError, SafetyError
from app.llm.events import Done, LLMEvent, ReasoningDelta, TextDelta, ToolCall, Usage
from app.llm.providers.alibaba import AlibabaProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.registry import default_registry
from app.services.generation import GenerationRegistry, GenerationService

ALIBABA_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
GEMINI_BASE_URL = "https://gemini.test"

# --- сборка SSE-тел и провайдеров поверх MockTransport ------------------------


def _ali_chunk(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> str:
    payload: dict[str, Any] = {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    payload.update(extra)
    return "data: " + json.dumps(payload)


def _sse_body(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode()


def _ali_usage_trailer(prompt: int, completion: int, total: int) -> str:
    """Usage-trailer Alibaba: отдельный чанк с пустым choices (после finish)."""
    usage = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}
    return "data: " + json.dumps({"choices": [], "usage": usage})


def _alibaba_provider(lines: list[str], calls: list[int] | None = None) -> AlibabaProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(1)
        return httpx.Response(
            200, content=_sse_body(lines), headers={"content-type": "text/event-stream"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=ALIBABA_BASE_URL)
    return AlibabaProvider(api_key="sk-test", base_url=ALIBABA_BASE_URL, http_client=client)


def _gemini_sse(*chunks: dict[str, Any] | str) -> bytes:
    frames = []
    for chunk in chunks:
        data = chunk if isinstance(chunk, str) else json.dumps(chunk)
        frames.append(f"data: {data}")
    return ("\n\n".join(frames) + "\n\n").encode()


def _gemini_provider(body: bytes) -> GeminiProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=GEMINI_BASE_URL)
    return GeminiProvider(base_url=GEMINI_BASE_URL, http_client=client)


def _ali_request(**kw: Any) -> LLMRequest:
    return LLMRequest(
        model="qwen3.8-flash",
        messages=[{"role": "user", "parts": [{"type": "text", "text": "hi"}]}],
        **kw,
    )


def _gemini_request(**kw: Any) -> LLMRequest:
    kw.setdefault("metadata", {"api_key": "test-key"})
    return LLMRequest(
        model="gemini-3.8-flash",
        messages=[{"role": "user", "parts": [{"type": "text", "text": "hi"}]}],
        **kw,
    )


async def _collect(stream: AsyncIterator[LLMEvent]) -> list[LLMEvent]:
    return [event async for event in stream]


# --- потребитель (consumer) уровня GenerationService._consume -----------------


class FakeStreamer:
    """Записывает append/finalize/fail без Telegram (стиль tests/unit/test_generation.py)."""

    def __init__(self) -> None:
        self.appended: list[str] = []
        self.finalize_calls = 0
        self.fail_calls: list[str] = []

    async def append(self, delta: str) -> None:
        self.appended.append(delta)

    async def flush(self, *, force: bool = False) -> None:
        pass

    async def finalize(self) -> None:
        self.finalize_calls += 1

    async def fail(self, user_message: str) -> None:
        self.fail_calls.append(user_message)


async def _empty_stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
    return
    yield  # pragma: no cover - async-генератор без событий


def _consumer_service() -> GenerationService:
    # _consume не трогает БД — session_factory не нужен (стиль test_generation.py).
    return GenerationService(
        session_factory=None,
        registry=default_registry(),
        llm_stream=_empty_stream,
        settings=Settings(default_model="gemini-3.8-flash"),
        generation_registry=GenerationRegistry(),
    )


# --- Alibaba: порядок событий, usage-trailer, EOF, malformed, fragmentation ---


async def test_alibaba_full_stream_usage_before_done_single_done() -> None:
    """text-чанки → finish → usage-trailer (choices: []) → [DONE].

    События: TextDelta*, Usage, Done. Usage ДО Done (usage-trailer приходит
    ПОСЛЕ finish_reason — парсер обязан буферизовать Done). Done ровно один.
    """
    lines = [
        _ali_chunk({"role": "assistant", "content": "Hel"}),
        _ali_chunk({"content": "lo"}),
        _ali_chunk({}, finish="stop"),
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
    events = await _collect(_alibaba_provider(lines).stream_chat(_ali_request()))
    assert events == [
        TextDelta("Hel"),
        TextDelta("lo"),
        Usage(input_tokens=10, output_tokens=5, reasoning_tokens=3, total_tokens=15),
        Done("stop"),
    ]
    assert sum(isinstance(e, Done) for e in events) == 1
    assert isinstance(events[-1], Done)
    assert [i for i, e in enumerate(events) if isinstance(e, Usage)] < [
        len(events) - 1
    ]  # usage строго до Done

    # Consumer-уровень: текст склеен, usage сохранён, finish зафиксирован.
    streamer = FakeStreamer()
    outcome = await _consumer_service()._consume(
        _alibaba_provider(lines).stream_chat(_ali_request()), streamer, asyncio.Event()
    )
    assert outcome.text == "Hello"
    assert streamer.appended == ["Hel", "lo"]
    assert outcome.usage == Usage(
        input_tokens=10, output_tokens=5, reasoning_tokens=3, total_tokens=15
    )
    assert outcome.finish_reason == "stop"
    assert outcome.cancelled is False


async def test_alibaba_eof_without_done_raises_network_error() -> None:
    """EOF без finish_reason и без [DONE] — NetworkError (unexpected EOF), не успех.

    Частичный текст уже ушёл потребителю (partial → ошибка без дублирования);
    повторных попыток провайдера нет, т.к. события уже эмитились.
    """
    calls: list[int] = []
    lines = [_ali_chunk({"content": "hi"})]  # поток обрывается без finish/[DONE]
    provider = _alibaba_provider(lines, calls)

    events: list[LLMEvent] = []
    with pytest.raises(NetworkError, match="unexpected EOF"):
        async for event in provider.stream_chat(_ali_request()):
            events.append(event)

    assert events == [TextDelta("hi")]  # partial дошёл до ошибки
    assert len(calls) == 1  # events_started → без retry
    assert not any(isinstance(e, Done) for e in events)  # Done не фабрикуется


async def test_alibaba_malformed_json_frame_skipped_stream_alive() -> None:
    """Битый JSON-фрейм в середине потока пропускается, поток продолжается."""
    lines = [
        _ali_chunk({"content": "a"}),
        "data: {not-json",
        ": comment-frame",  # не-data строка тоже пропускается
        _ali_chunk({"content": "b"}),
        _ali_chunk({}, finish="stop"),
        _ali_usage_trailer(3, 2, 5),
        "data: [DONE]",
    ]
    events = await _collect(_alibaba_provider(lines).stream_chat(_ali_request()))
    assert events == [
        TextDelta("a"),
        TextDelta("b"),
        Usage(input_tokens=3, output_tokens=2, total_tokens=5),
        Done("stop"),
    ]


async def test_alibaba_fragmented_tool_args_single_tool_call() -> None:
    """Аргументы tool call, разрезанные на 3 чанка, собираются в ОДИН ToolCall
    с полным JSON; далее usage-trailer, затем единственный Done."""
    lines = [
        _ali_chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_42",
                        "function": {"name": "web_search", "arguments": '{"q": "poly'},
                    }
                ]
            }
        ),
        _ali_chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'ra", "n":'}}]}),
        _ali_chunk({"tool_calls": [{"index": 0, "function": {"arguments": " 1}"}}]}),
        _ali_chunk({}, finish="tool_calls"),
        _ali_usage_trailer(8, 4, 12),
        "data: [DONE]",
    ]
    events = await _collect(_alibaba_provider(lines).stream_chat(_ali_request()))

    tool_calls = [e for e in events if isinstance(e, ToolCall)]
    assert len(tool_calls) == 1  # ровно один ToolCall, не по одному на чанк
    call = tool_calls[0]
    assert call.id == "call_42"
    assert call.name == "web_search"
    assert json.loads(call.arguments_json) == {"q": "polyra", "n": 1}
    assert events == [
        call,
        Usage(input_tokens=8, output_tokens=4, total_tokens=12),
        Done("tool_calls"),
    ]
    assert sum(isinstance(e, Done) for e in events) == 1


async def test_alibaba_cancellation_before_iteration_yields_nothing() -> None:
    """cancellation выставлен до начала итерации → ни одного события, без ошибок."""
    cancellation = asyncio.Event()
    cancellation.set()
    lines = [_ali_chunk({"content": "hi"}), _ali_chunk({}, finish="stop"), "data: [DONE]"]
    events = await _collect(
        _alibaba_provider(lines).stream_chat(_ali_request(cancellation=cancellation))
    )
    assert events == []


# --- Gemini: reasoning, tool+signature, usage+STOP, safety, EOF ----------------


async def test_gemini_thought_part_becomes_reasoning_delta_never_text() -> None:
    """part с thought=True → ReasoningDelta, НИКОГДА не TextDelta (ни в поток,
    ни в потребительский текст)."""
    body = _gemini_sse(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "скрытая мысль", "thought": True}, {"text": "ответ"}]
                    },
                    "finishReason": "STOP",
                }
            ]
        }
    )
    events = await _collect(_gemini_provider(body).stream_chat(_gemini_request()))
    assert events == [ReasoningDelta("скрытая мысль"), TextDelta("ответ"), Done("stop")]
    assert not any(isinstance(e, TextDelta) and e.text == "скрытая мысль" for e in events)

    streamer = FakeStreamer()
    outcome = await _consumer_service()._consume(
        _gemini_provider(body).stream_chat(_gemini_request()), streamer, asyncio.Event()
    )
    assert outcome.text == "ответ"
    assert outcome.reasoning_chunks == 1
    assert streamer.appended == ["ответ"]  # мысль не ушла в драфт


async def test_gemini_function_call_with_thought_signature_tool_call() -> None:
    """functionCall + thoughtSignature → один ToolCall с provider_meta
    (thought_signature сохраняется для возврата в историю as-is, A39)."""
    body = _gemini_sse(
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
    events = await _collect(_gemini_provider(body).stream_chat(_gemini_request()))
    assert len(events) == 2
    call = events[0]
    assert isinstance(call, ToolCall)
    assert call.name == "web_search"
    assert json.loads(call.arguments_json) == {"q": "x"}
    assert call.provider_meta["thought_signature"] == "sig123"
    assert call.id  # id синтезируется, если провайдер не дал
    assert events[1] == Done("stop")


async def test_gemini_usage_and_stop_same_chunk_usage_before_done() -> None:
    """usageMetadata и finishReason=STOP в ОДНОМ чанке → Usage строго перед Done."""
    body = _gemini_sse(
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
    events = await _collect(_gemini_provider(body).stream_chat(_gemini_request()))
    assert events == [
        Usage(input_tokens=10, output_tokens=5, reasoning_tokens=2, total_tokens=17),
        TextDelta("ok"),
        Done("stop"),
    ]
    assert isinstance(events[-1], Done)
    usage_idx = next(i for i, e in enumerate(events) if isinstance(e, Usage))
    done_idx = next(i for i, e in enumerate(events) if isinstance(e, Done))
    assert usage_idx < done_idx

    streamer = FakeStreamer()
    outcome = await _consumer_service()._consume(
        _gemini_provider(body).stream_chat(_gemini_request()), streamer, asyncio.Event()
    )
    assert outcome.usage == Usage(
        input_tokens=10, output_tokens=5, reasoning_tokens=2, total_tokens=17
    )
    assert outcome.finish_reason == "stop"


async def test_gemini_prompt_feedback_block_reason_raises_safety() -> None:
    """promptFeedback.blockReason → SafetyError, до ошибки событий нет."""
    body = _gemini_sse({"promptFeedback": {"blockReason": "SAFETY"}})
    events: list[LLMEvent] = []
    with pytest.raises(SafetyError):
        async for event in _gemini_provider(body).stream_chat(_gemini_request()):
            events.append(event)
    assert events == []


async def test_gemini_cancellation_before_iteration_yields_nothing() -> None:
    """cancellation выставлен до начала итерации → ни одного события, без ошибок."""
    cancellation = asyncio.Event()
    cancellation.set()
    body = _gemini_sse(
        {"candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}]}
    )
    events = await _collect(
        _gemini_provider(body).stream_chat(_gemini_request(cancellation=cancellation))
    )
    assert events == []


async def test_done_emitted_exactly_once_per_stream() -> None:
    """Контракт §1: ровно один терминальный Done на поток — оба провайдера."""
    ali_lines = [
        _ali_chunk({"content": "x"}),
        _ali_chunk({}, finish="stop"),
        _ali_usage_trailer(1, 1, 2),
        "data: [DONE]",
    ]
    ali_events = await _collect(_alibaba_provider(ali_lines).stream_chat(_ali_request()))
    assert sum(isinstance(e, Done) for e in ali_events) == 1
    assert isinstance(ali_events[-1], Done)

    gem_body = _gemini_sse(
        {"candidates": [{"content": {"parts": [{"text": "x"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "y"}]}, "finishReason": "STOP"}]},
    )
    gem_events = await _collect(_gemini_provider(gem_body).stream_chat(_gemini_request()))
    assert sum(isinstance(e, Done) for e in gem_events) == 1
    assert isinstance(gem_events[-1], Done)


async def test_gemini_eof_without_done_is_error_contract() -> None:
    """(бывший xfail-blocker, исправлено в FIX V2) EOF без finishReason/[DONE]
    → ошибка unexpected EOF, а не тихий успех с обрезанным текстом."""
    body = _gemini_sse({"candidates": [{"content": {"parts": [{"text": "partial"}]}}]})
    events: list[LLMEvent] = []
    with pytest.raises(NetworkError):
        async for event in _gemini_provider(body).stream_chat(_gemini_request()):
            events.append(event)
    assert events == [TextDelta("partial")]  # partial до ошибки, без фабрикации Done
