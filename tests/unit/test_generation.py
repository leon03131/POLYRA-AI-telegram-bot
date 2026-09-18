"""Unit-тесты сервиса генерации (app.services.generation).

Покрытие: GenerationRegistry (register/find/stop/pop), чистые функции
user_error_message / resolve_model_and_thinking / build_messages и потребление
стрима _consume (фейковый async-генератор событий + фейковый streamer).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm.base import LLMRequest
from app.llm.errors import (
    AuthError,
    InvalidRequestError,
    NetworkError,
    RateLimitError,
    SafetyError,
    ServerError,
    TimeoutError_,
)
from app.llm.events import Done, LLMEvent, ReasoningDelta, TextDelta, ToolCall, ToolResult, Usage
from app.llm.gemini.pool import PoolExhaustedError
from app.llm.registry import default_registry
from app.llm.tools.registry import ToolDefinition, ToolRegistry
from app.llm.tools.runner import ToolExecution, ToolRunner
from app.services.access import evaluate_access
from app.services.generation import (
    ActiveGeneration,
    GenerationRegistry,
    GenerationService,
    _PreparedGeneration,
    build_messages,
    build_sources_suffix,
    collect_sources,
    extract_sources,
    resolve_model_and_thinking,
    user_error_message,
)

# --- GenerationRegistry ------------------------------------------------------


def _gen(chat_id: uuid.UUID, tg_chat_id: int = 1, draft_id: int = 1) -> ActiveGeneration:
    task = asyncio.current_task()
    assert task is not None
    return ActiveGeneration(
        task=task,
        cancellation=asyncio.Event(),
        draft_id=draft_id,
        tg_chat_id=tg_chat_id,
        chat_id=chat_id,
    )


async def test_registry_register_find_stop_pop() -> None:
    registry = GenerationRegistry()
    chat_a, chat_b = uuid.uuid4(), uuid.uuid4()
    gen_a = _gen(chat_a, tg_chat_id=100, draft_id=1)
    gen_b = _gen(chat_b, tg_chat_id=200, draft_id=2)
    registry.register(gen_a)
    registry.register(gen_b)

    assert registry.find_active_for_chat(chat_a) is gen_a
    assert registry.find_active_for_chat(uuid.uuid4()) is None
    assert registry.find_by_draft(200, 2) is gen_b
    assert registry.find_by_draft(200, 999) is None

    assert await registry.stop(100, 1) is True
    assert gen_a.cancellation.is_set()
    assert gen_b.cancellation.is_set() is False

    assert registry.pop(100, 1) is gen_a
    assert registry.pop(100, 1) is None
    assert await registry.stop(100, 1) is False
    assert registry.find_active_for_chat(chat_a) is None


async def test_registry_single_active_per_chat_contract() -> None:
    """Контракт «одна активная генерация на чат»: пока gen не pop'нута,
    find_active_for_chat её находит (generate() в этом случае отвечает ⏳)."""
    registry = GenerationRegistry()
    chat_id = uuid.uuid4()
    registry.register(_gen(chat_id, tg_chat_id=1, draft_id=1))
    assert registry.find_active_for_chat(chat_id) is not None
    # После pop чат свободен — следующая генерация регистрируется.
    registry.pop(1, 1)
    gen2 = _gen(chat_id, tg_chat_id=1, draft_id=2)
    registry.register(gen2)
    assert registry.find_active_for_chat(chat_id) is gen2


# --- user_error_message -------------------------------------------------------


def test_user_error_message_pool_exhausted_includes_model() -> None:
    text = user_error_message(PoolExhaustedError("gemini-3.8-flash"), "Gemini 3.8 Flash")
    assert "Gemini 3.8 Flash" in text
    assert "недоступн" in text


@pytest.mark.parametrize(
    ("exc", "fragment"),
    [
        (RateLimitError("quota"), "429"),
        (AuthError("bad key"), "авторизац"),
        (SafetyError("blocked"), "модерац"),
        (InvalidRequestError("bad request"), "некоррект"),
        (TimeoutError_("slow"), "недоступен"),
        (NetworkError("down"), "недоступен"),
        (ServerError("500"), "недоступен"),
    ],
)
def test_user_error_message_categories(exc: Exception, fragment: str) -> None:
    text = user_error_message(exc, "Model X")
    assert fragment in text
    assert str(exc) not in text  # без внутренних деталей


def test_user_error_message_generic_hides_details() -> None:
    text = user_error_message(ValueError("secret internals"), "Model X")
    assert "secret internals" not in text
    assert text


# --- resolve_model_and_thinking ----------------------------------------------


def _chat(model_id: str | None = None, thinking: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(model_id=model_id, thinking_setting=thinking)


def _user_settings(
    default_model_id: str | None = None, default_thinking: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(default_model_id=default_model_id, default_thinking=default_thinking)


_SETTINGS = Settings(default_model="gemini-3.8-flash")


def test_resolve_prefers_chat_over_user_and_global() -> None:
    model_id, thinking = resolve_model_and_thinking(
        chat=_chat("qwen3.8-flash", "low"),
        user_settings=_user_settings("gemini-3.7-flash", "high"),
        settings=_SETTINGS,
        registry=default_registry(),
    )
    assert (model_id, thinking) == ("qwen3.8-flash", "low")


def test_resolve_user_default_when_chat_empty() -> None:
    model_id, thinking = resolve_model_and_thinking(
        chat=_chat(),
        user_settings=_user_settings("gemini-3.7-flash", "high"),
        settings=_SETTINGS,
        registry=default_registry(),
    )
    assert (model_id, thinking) == ("gemini-3.7-flash", "high")


def test_resolve_global_default_and_model_default_thinking() -> None:
    model_id, thinking = resolve_model_and_thinking(
        chat=_chat(),
        user_settings=_user_settings(),
        settings=_SETTINGS,
        registry=default_registry(),
    )
    assert model_id == "gemini-3.8-flash"
    assert thinking == "medium"  # default_thinking модели


def test_resolve_unknown_model_falls_back_to_default() -> None:
    model_id, _ = resolve_model_and_thinking(
        chat=_chat("no-such-model"),
        user_settings=_user_settings(),
        settings=_SETTINGS,
        registry=default_registry(),
    )
    assert model_id == "gemini-3.8-flash"


def test_resolve_thinking_outside_modes_becomes_none() -> None:
    # У gemini-3.8-flash modes = (low, medium, high); "max" не входит → None.
    _, thinking = resolve_model_and_thinking(
        chat=_chat("gemini-3.8-flash", "max"),
        user_settings=_user_settings(),
        settings=_SETTINGS,
        registry=default_registry(),
    )
    assert thinking is None


# --- build_messages -----------------------------------------------------------


def _history_message(role: str, parts: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(role=role, parts=[SimpleNamespace(**part) for part in parts])


def test_build_messages_text_history_plus_current_as_is() -> None:
    history = [
        _history_message("user", [{"type": "text", "text": "привет"}]),
        _history_message("assistant", [{"type": "text", "text": "здравствуй"}]),
    ]
    current = [
        {"type": "image", "mime_type": "image/jpeg", "data_base64": "AA=="},
        {"type": "text", "text": "что на фото?"},
    ]
    messages = build_messages(history=history, current_parts=current)
    assert messages == [
        {"role": "user", "parts": [{"type": "text", "text": "привет"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "здравствуй"}]},
        {"role": "user", "parts": current},
    ]


def test_build_messages_skips_image_parts_in_history() -> None:
    history = [
        _history_message(
            "user",
            [
                {"type": "image", "text": None, "mime_type": "image/jpeg"},
                {"type": "text", "text": "подпись"},
            ],
        )
    ]
    messages = build_messages(history=history, current_parts=[{"type": "text", "text": "ok"}])
    assert messages[0] == {"role": "user", "parts": [{"type": "text", "text": "подпись"}]}


def test_build_messages_drops_image_only_history_message() -> None:
    history = [_history_message("user", [{"type": "image", "text": None}])]
    messages = build_messages(history=history, current_parts=[{"type": "text", "text": "ok"}])
    assert messages == [{"role": "user", "parts": [{"type": "text", "text": "ok"}]}]


# --- _consume -----------------------------------------------------------------


class FakeStreamer:
    """Записывает append/finalize/fail без Telegram."""

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


async def _stream_of(events: list[LLMEvent]) -> AsyncIterator[LLMEvent]:
    for event in events:
        yield event


def _unused_llm_stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
    return _stream_of([])


def _service() -> GenerationService:
    # _consume не трогает БД/конфиг — session_factory не нужен.
    return GenerationService(
        session_factory=None,
        registry=default_registry(),
        llm_stream=_unused_llm_stream,
        settings=_SETTINGS,
        generation_registry=GenerationRegistry(),
    )


async def test_consume_appends_text_saves_usage_breaks_on_done() -> None:
    streamer = FakeStreamer()
    events: list[LLMEvent] = [
        ReasoningDelta("думаю"),
        TextDelta("При"),
        TextDelta("вет"),
        Usage(input_tokens=10, output_tokens=2),
        Done("stop"),
        TextDelta("после Done не доходит"),
    ]
    outcome = await _service()._consume(_stream_of(events), streamer, asyncio.Event())
    assert outcome.text == "Привет"
    assert streamer.appended == ["При", "вет"]
    assert outcome.usage == Usage(input_tokens=10, output_tokens=2)
    assert outcome.cancelled is False
    assert outcome.first_token_at is not None
    assert outcome.reasoning_chunks == 1


async def test_consume_tool_call_counted_not_appended() -> None:
    streamer = FakeStreamer()
    events: list[LLMEvent] = [
        ToolCall(id="c1", name="search", arguments_json="{}"),
        Done("tool_calls"),
    ]
    outcome = await _service()._consume(_stream_of(events), streamer, asyncio.Event())
    assert outcome.tool_calls_count == 1
    assert streamer.appended == []
    assert outcome.cancelled is False


async def test_consume_stops_on_cancellation_with_partial() -> None:
    cancellation = asyncio.Event()

    class CancellingStreamer(FakeStreamer):
        async def append(self, delta: str) -> None:
            await super().append(delta)
            if len(self.appended) == 2:
                cancellation.set()

    async def infinite() -> AsyncIterator[LLMEvent]:
        while True:
            yield TextDelta("x")

    streamer = CancellingStreamer()
    outcome = await _service()._consume(infinite(), streamer, cancellation)
    assert outcome.cancelled is True
    assert outcome.text == "xx"


async def test_consume_cancelled_error_returns_partial() -> None:
    async def boom() -> AsyncIterator[LLMEvent]:
        yield TextDelta("part")
        raise asyncio.CancelledError

    outcome = await _service()._consume(boom(), FakeStreamer(), asyncio.Event())
    assert outcome.cancelled is True
    assert outcome.text == "part"


async def test_consume_quiet_stream_end_with_cancellation_flag() -> None:
    """Провайдер тихо завершил генератор по cancellation (без Done) → cancelled."""
    cancellation = asyncio.Event()

    async def quiet() -> AsyncIterator[LLMEvent]:
        yield TextDelta("abc")
        cancellation.set()
        return

    outcome = await _service()._consume(quiet(), FakeStreamer(), cancellation)
    assert outcome.cancelled is True
    assert outcome.text == "abc"


# --- tool loop / sources (M8) ------------------------------------------------


def test_extract_sources_numbered_list() -> None:
    content = "1. Первый\nhttps://a.example\nsnippet\n2. Второй\nhttps://b.example\nsnippet"
    sources = extract_sources(content)
    assert sources == [("Первый", "https://a.example"), ("Второй", "https://b.example")]


def test_extract_sources_marker_line() -> None:
    sources = extract_sources("текст\nSOURCES: https://a.example | https://b.example")
    assert sources == [
        ("https://a.example", "https://a.example"),
        ("https://b.example", "https://b.example"),
    ]


def test_collect_sources_dedupe_and_skip_errors() -> None:
    call = ToolCall(id="1", name="web_search", arguments_json="{}")
    ok_execution = ToolExecution(
        call=call,
        result=ToolResult(
            call_id="1",
            name="web_search",
            content="1. A\nhttps://a.example\nSOURCES: https://a.example | https://b.example",
        ),
        status="ok",
        duration_ms=1,
    )
    err_execution = ToolExecution(
        call=call,
        result=ToolResult(
            call_id="1", name="web_search", content="1. X\nhttps://x.example", is_error=True
        ),
        status="error",
        duration_ms=1,
    )
    sources = collect_sources([(call, ok_execution), (call, err_execution)])
    assert [url for _, url in sources] == ["https://a.example", "https://b.example"]


def test_build_sources_suffix() -> None:
    suffix = build_sources_suffix([("A", "https://a.example"), ("B", "https://b.example")])
    assert "Источники" in suffix
    assert "1. [A](https://a.example)" in suffix
    assert "2. [B](https://b.example)" in suffix


def _prepared_stub() -> _PreparedGeneration:
    return _PreparedGeneration(
        chat_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        draft_id=1,
        model_id="gemini-3.8-flash",
        model_display="Gemini 3.8 Flash",
        provider="gemini",
        thinking=None,
        system_prompt="sys",
        messages=[{"role": "user", "parts": [{"type": "text", "text": "hi"}]}],
        web_enabled=True,
    )


def _permissions_stub() -> object:
    return evaluate_access(
        is_owner=True,
        user_status="active",
        grant=None,
        allowed_models=None,
        now=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )


async def test_stream_loop_executes_tool_calls_and_continues() -> None:
    """Первый раунд — tool_call, второй — текст; результат инструмента в истории."""
    calls: list[LLMRequest] = []

    def fake_stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
        calls.append(request)
        if len(calls) == 1:
            return _stream_of(
                [
                    ToolCall(id="c1", name="echo", arguments_json='{"text": "hi"}'),
                    Done("tool_calls"),
                ]
            )
        return _stream_of([TextDelta("готово"), Done("stop")])

    async def echo_handler(args: dict, context: object) -> str:
        return "echo: " + str(args["text"])

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo",
            description="echo",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=echo_handler,
        )
    )
    service = _service()
    service._llm_stream = fake_stream
    runner = ToolRunner(registry, session_factory=None)
    streamer = FakeStreamer()

    result = await service._stream_loop(
        _prepared_stub(),
        streamer,
        llm_tools=None,
        tool_runner=runner,
        user=SimpleNamespace(id=uuid.uuid4()),
        permissions=_permissions_stub(),
        cancellation=asyncio.Event(),
    )

    assert result is not None
    outcome, tool_records = result
    assert outcome.text == "готово"
    assert outcome.cancelled is False
    assert outcome.tool_calls_count == 1
    # второй запрос содержит assistant tool_call + tool result
    second_messages = calls[1].messages
    assert any(m["role"] == "assistant" for m in second_messages)
    tool_msgs = [m for m in second_messages if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["parts"][0]["content"] == "echo: hi"


async def test_stream_loop_respects_max_iterations() -> None:
    """Модель бесконечно просит tool — цикл останавливается по лимиту."""

    def looping_stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
        return _stream_of([ToolCall(id="c", name="echo", arguments_json="{}"), Done("tool_calls")])

    async def echo_handler(args: dict, context: object) -> str:
        return "ok: " + str(args)[:20]

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo", description="echo", parameters={"type": "object"}, handler=echo_handler
        )
    )
    settings = Settings(max_tool_iterations=2)
    service = GenerationService(
        session_factory=None,
        registry=default_registry(),
        llm_stream=looping_stream,
        settings=settings,
        generation_registry=GenerationRegistry(),
    )
    runner = ToolRunner(registry, session_factory=None)

    result = await service._stream_loop(
        _prepared_stub(),
        FakeStreamer(),
        llm_tools=None,
        tool_runner=runner,
        user=SimpleNamespace(id=uuid.uuid4()),
        permissions=_permissions_stub(),
        cancellation=asyncio.Event(),
    )

    assert result is not None
    outcome, tool_records = result
    assert len(tool_records) == 2  # ровно max_tool_iterations исполнений
    assert outcome.cancelled is False

async def test_stream_loop_runs_tools_when_finish_stop_but_calls_present() -> None:
    """Gemini-стиль: finish_reason='stop' + ToolCall в потоке → tools исполняются."""
    calls: list[LLMRequest] = []

    def fake_stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
        calls.append(request)
        if len(calls) == 1:
            # КЛЮЧЕВОЕ: finish_reason НЕ tool_calls (как у Gemini)
            return _stream_of(
                [ToolCall(id="c1", name="echo", arguments_json='{"text": "hi"}'), Done("stop")]
            )
        return _stream_of([TextDelta("ответ"), Done("stop")])

    async def echo_handler(args: dict, context: object) -> str:
        return "echo: " + str(args["text"])

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo",
            description="echo",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=echo_handler,
        )
    )
    service = _service()
    service._llm_stream = fake_stream
    runner = ToolRunner(registry, session_factory=None)

    result = await service._stream_loop(
        _prepared_stub(),
        FakeStreamer(),
        llm_tools=None,
        tool_runner=runner,
        user=SimpleNamespace(id=uuid.uuid4()),
        permissions=_permissions_stub(),
        cancellation=asyncio.Event(),
    )

    assert result is not None
    outcome, tool_records = result
    assert len(tool_records) == 1  # tool исполнен несмотря на finish_reason=stop
    assert outcome.text == "ответ"
    assert len(calls) == 2  # второй раунд с результатом инструмента


async def test_stream_loop_empty_final_text_gives_no_crash() -> None:
    """Модель вернула пустой текст без tool calls — цикл завершается, текст пустой."""
    service = _service()
    service._llm_stream = lambda request: _stream_of([Done("stop")])

    result = await service._stream_loop(
        _prepared_stub(),
        FakeStreamer(),
        llm_tools=None,
        tool_runner=None,
        user=SimpleNamespace(id=uuid.uuid4()),
        permissions=_permissions_stub(),
        cancellation=asyncio.Event(),
    )
    assert result is not None
    outcome, tool_records = result
    assert outcome.text == ""
    assert tool_records == []
