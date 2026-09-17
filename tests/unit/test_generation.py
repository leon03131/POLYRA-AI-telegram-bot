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
from app.llm.events import Done, LLMEvent, ReasoningDelta, TextDelta, ToolCall, Usage
from app.llm.gemini.pool import PoolExhaustedError
from app.llm.registry import default_registry
from app.services.generation import (
    ActiveGeneration,
    GenerationRegistry,
    GenerationService,
    build_messages,
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
