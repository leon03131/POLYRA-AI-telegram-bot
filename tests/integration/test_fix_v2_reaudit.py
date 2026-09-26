"""FIX_V2 re-audit (батч GLM, commit 0be0524) — регрессии A20/A39/A15.

Offline, без сети и БД: фейковый LLM-стрим по сценарию, GenerationService
на фейковых репозиториях/сессиях (monkeypatch модуля) и записывающий
фейк-бот (стиль tests/integration/test_fix_v2_usage_ledger.py).
- A20: stop-partial уходит обычными сообщениями с parse_mode=None и
  разбивкой по 4096 — HTML-опасный текст не парсится и не обрезается.
- A39: при batch tool calls больше max_tool_calls_per_round в assistant
  turn следующего запроса остаются только ИСПОЛНЯЕМЫЕ вызовы — нет
  «висящих» tool_call без tool_result (400 у strict-провайдеров).
- A15: при наличии сводки только что записанное user-сообщение не
  дублируется в контексте (list_all фильтрует его; current идёт отдельно).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import app.services.generation as generation_module
from app.bot.streaming.draft import MESSAGE_LIMIT
from app.config import Settings
from app.context import ContextBuilder, TokenBudgetManager
from app.llm.base import LLMRequest
from app.llm.events import Done, LLMEvent, TextDelta, ToolCall
from app.llm.registry import default_registry
from app.llm.tools.registry import ToolDefinition, ToolRegistry
from app.llm.tools.runner import ToolRunner
from app.llm.tools.schemas import make_llm_tools
from app.services.access import evaluate_access
from app.services.generation import GenerationRegistry, GenerationService, _PreparedGeneration

_SETTINGS = Settings(default_model="gemini-3.8-flash")

_PART_HEAD = "<div>частичный ответ модели</div>"  # HTML-опасный фрагмент partial (A20)
_CURRENT_MARKER = "MARKER-CURRENT-MSG"  # уникальный маркер текущего сообщения (A15)


# --- общие фейки (по образцу test_fix_v2_usage_ledger.py) ----------------------


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


async def _empty_stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
    return
    yield  # pragma: no cover - async-генератор без событий


def _permissions_stub() -> Any:
    return evaluate_access(
        is_owner=True,
        user_status="active",
        grant=None,
        allowed_models=None,
        now=datetime.now(UTC),
    )


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


def _echo_engine() -> tuple[ToolRegistry, ToolRunner, list[Any]]:
    registry = ToolRegistry()

    async def echo_handler(args: dict, context: object) -> str:
        return "echo: " + str(args["text"])

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
    runner = ToolRunner(registry, session_factory=None)
    llm_tools = make_llm_tools(registry.list_enabled(_permissions_stub()))
    return registry, runner, llm_tools


def _service(llm_stream: Any = None, settings: Settings | None = None) -> GenerationService:
    return GenerationService(
        session_factory=None,
        registry=default_registry(),
        llm_stream=llm_stream or _empty_stream,
        settings=settings or _SETTINGS,
        generation_registry=GenerationRegistry(),
    )


class _FakeSession:
    async def commit(self) -> None:
        pass


class _FakeSessionFactory:
    """async_sessionmaker-совместимый фейк: вызов → async CM, yielding session."""

    def __call__(self) -> _FakeSessionFactory:
        return self

    async def __aenter__(self) -> _FakeSession:
        return _FakeSession()

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeMessageRepository:
    calls: list[dict[str, Any]] = []

    def __init__(self, session: Any) -> None:
        pass

    async def add_message(self, chat_id: Any, role: str, **kw: Any) -> None:
        type(self).calls.append(kw)


class _FakeChatRepository:
    def __init__(self, session: Any) -> None:
        pass

    async def touch(self, chat_id: Any) -> None:
        pass


class _FakeGenerationRunRepository:
    finish_calls: list[dict[str, Any]] = []

    def __init__(self, session: Any) -> None:
        pass

    async def finish(self, run_id: Any, **kw: Any) -> None:
        type(self).finish_calls.append(kw)


class _RecordingBot:
    """Фейк-бот: send_message фиксируется как (chat_id, text, kwargs)."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str, dict[str, Any]]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.calls.append((chat_id, text, kwargs))


class _FakeChatSummaryRepository:
    """ChatSummaryRepository-фейк: get_for_chat возвращает заданную сводку."""

    summary_row: Any = None

    def __init__(self, session: Any) -> None:
        pass

    async def get_for_chat(self, chat_id: uuid.UUID) -> Any:
        return type(self).summary_row


class _FakeMessagesRepo:
    """MessageRepository-фейк: list_all возвращает историю, УЖЕ содержащую
    только что добавленное user-сообщение (имитация add_message → list_all
    в той же сессии)."""

    def __init__(self, history: list[Any]) -> None:
        self._history = history
        self.list_all_calls: list[uuid.UUID] = []

    async def list_all(self, chat_id: uuid.UUID) -> list[Any]:
        self.list_all_calls.append(chat_id)
        return list(self._history)


def _history_message(role: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        role=role,
        parts=[SimpleNamespace(type="text", text=text)],
    )


def _parts_of(
    messages: list[dict[str, Any]], role: str, part_type: str
) -> list[dict[str, Any]]:
    """Части типа part_type из сообщений с ролью role (нормализованный формат)."""
    return [
        part
        for message in messages
        if message.get("role") == role
        for part in message.get("parts", [])
        if part.get("type") == part_type
    ]


def _marker_occurrences(messages: list[dict[str, Any]], marker: str) -> int:
    """Сколько text-частей user-сообщений содержат marker."""
    return sum(
        1
        for part in _parts_of(messages, "user", "text")
        if marker in str(part.get("text") or "")
    )


# --- A20: stop-partial — parse_mode=None и разбивка по 4096 ---------------------


async def test_save_cancelled_partial_plain_parse_mode_none_and_split(
    monkeypatch: Any,
) -> None:
    """A20 (commit 0be0524): отмена стрима на длинном HTML-опасном partial →
    _save_cancelled шлёт его обычными сообщениями с parse_mode=None и
    разбивкой _split_text(4096): полный текст, без обрезки и без HTML-парсинга.

    До фикса: send_message вызывался БЕЗ parse_mode (бот с default HTML
    упал бы на «<div>») и с обрезкой до 4095 + «…» — оба ассерта ниже красные.
    """
    _FakeMessageRepository.calls = []
    _FakeGenerationRunRepository.finish_calls = []
    monkeypatch.setattr(generation_module, "MessageRepository", _FakeMessageRepository)
    monkeypatch.setattr(generation_module, "ChatRepository", _FakeChatRepository)
    monkeypatch.setattr(generation_module, "GenerationRunRepository", _FakeGenerationRunRepository)

    cancellation = asyncio.Event()

    async def fake_stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
        yield TextDelta(_PART_HEAD)
        yield TextDelta("д" * 5000)
        cancellation.set()  # пользователь нажал Stop на середине стрима
        yield TextDelta("это не доехало до потребителя")
        yield Done("stop")

    service = GenerationService(
        session_factory=_FakeSessionFactory(),
        registry=default_registry(),
        llm_stream=fake_stream,
        settings=_SETTINGS,
        generation_registry=GenerationRegistry(),
    )

    result = await service._stream_loop(
        _prepared_stub(),
        FakeStreamer(),
        llm_tools=None,
        tool_runner=None,
        user=SimpleNamespace(id=uuid.uuid4()),
        permissions=_permissions_stub(),
        cancellation=cancellation,
    )
    assert result is not None
    outcome, _tool_records, _attempts, _attempt_ids = result
    assert outcome.cancelled is True
    assert "<div>" in outcome.text  # HTML-опасный фрагмент в partial
    assert len(outcome.text) > MESSAGE_LIMIT  # A21: разбивка, а не обрезка

    bot = _RecordingBot()
    await service._save_cancelled(_prepared_stub(), outcome, bot=bot, tg_chat_id=42)

    # (a) хотя бы один send_message вызван
    assert bot.calls
    # (b) parse_mode ЯВНО передан и is None у КАЖДОГО вызова
    #     (default бота — HTML: с «<div>» это BadRequest, partial терялся бы).
    for sent_chat_id, _sent_text, sent_kwargs in bot.calls:
        assert "parse_mode" in sent_kwargs
        assert sent_kwargs["parse_mode"] is None
        assert sent_chat_id == 42
    # (c) partial > 4096 разбит: несколько частей, каждая <= 4096,
    #     конкатенация в порядке отправки == полному partial.
    sent_texts = [sent_text for _chat_id, sent_text, _kwargs in bot.calls]
    assert len(sent_texts) > 1
    assert all(len(text) <= MESSAGE_LIMIT for text in sent_texts)
    assert "".join(sent_texts) == outcome.text
    # (d) HTML-опасный фрагмент действительно уезжает в сообщении
    #     — parse_mode=None обязателен.
    assert any("<div>" in text for text in sent_texts)
    # Персистенс отменённого запуска не потерялся (стиль §2 usage ledger).
    assert _FakeMessageRepository.calls and _FakeMessageRepository.calls[0]["status"] == (
        "cancelled"
    )
    assert _FakeGenerationRunRepository.finish_calls


# --- A39: orphan tool_calls при batch > max_tool_calls_per_round ----------------


async def test_tool_round_batch_over_limit_keeps_only_executed_calls_in_history() -> None:
    """A39 (commit 0be0524): 5 ToolCall одним раундом при
    max_tool_calls_per_round=4 → в СЛЕДУЮЩЕМ запросе ровно 4 tool_call-части
    в assistant-turn и ровно 4 tool_result-сообщения; множества id совпадают
    (нет «висящих» вызовов без результата), 5-й вызов не протекает.

    До фикса: assistant-turn содержал ВСЕ 5 вызовов, а результаты — только 4
    → (a) 5 != 4, (c) call_4 без tool_result, (d) call_4 в запросе — красный.
    """
    calls: list[LLMRequest] = []

    def fake_stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
        calls.append(request)
        if len(calls) == 1:
            batch: list[LLMEvent] = [
                ToolCall(id=f"call_{i}", name="echo", arguments_json=f'{{"text": "q{i}"}}')
                for i in range(5)
            ]
            return _stream_of([*batch, Done("tool_calls")])
        return _stream_of([TextDelta("готово"), Done("stop")])

    _registry, runner, llm_tools = _echo_engine()
    settings = Settings(default_model="gemini-3.8-flash", max_tool_calls_per_round=4)
    service = _service(fake_stream, settings)

    result = await service._stream_loop(
        _prepared_stub(),
        FakeStreamer(),
        llm_tools=llm_tools,
        tool_runner=runner,
        user=SimpleNamespace(id=uuid.uuid4()),
        permissions=_permissions_stub(),
        cancellation=asyncio.Event(),
    )

    assert result is not None
    outcome, tool_records, _attempts, _attempt_ids = result
    assert outcome.cancelled is False
    assert outcome.text == "готово"
    assert len(calls) == 2  # раунд tool calls → ровно два LLM-запроса
    assert len(tool_records) == 4  # исполнены только первые 4 из 5

    second_messages = calls[1].messages
    # (a) в assistant-сообщении предыдущего раунда ровно 4 tool_call-части.
    assistant_tool_call_parts = _parts_of(second_messages, "assistant", "tool_call")
    assert len(assistant_tool_call_parts) == 4
    # (b) ровно 4 tool_result-сообщения (по одному на исполненный вызов).
    tool_messages = [m for m in second_messages if m.get("role") == "tool"]
    tool_result_parts = _parts_of(second_messages, "tool", "tool_result")
    assert len(tool_messages) == 4
    assert len(tool_result_parts) == 4
    # (c) множество id tool_call-частей == множеству id tool_result.
    call_ids = {part["id"] for part in assistant_tool_call_parts}
    result_ids = {part["call_id"] for part in tool_result_parts}
    assert call_ids == result_ids
    assert call_ids == {"call_0", "call_1", "call_2", "call_3"}
    # (d) вызов с индексом 4+ не встречается в следующем запросе ни в каком
    #     виде (id, arguments, содержимое tool_result).
    assert "call_4" not in json.dumps(second_messages, ensure_ascii=False, default=str)


# --- A15: дубль current-сообщения при наличии сводки ----------------------------


async def test_build_context_with_summary_does_not_duplicate_current_message(
    monkeypatch: Any,
) -> None:
    """A15 (commit 0be0524): _build_context с exclude_message_id фильтрует
    только что записанное user-сообщение из history (list_all при сводке) —
    оно не дублируется: current_parts идут в запрос ровно один раз.

    До фикса: параметра exclude_message_id не было вовсе (TypeError), а без
    фильтра list_all возвращал текущее сообщение → маркер встречался бы ДВАЖДЫ
    (история + отдельно current) — ассерт (a) красный.

    P0-регрессия коммита 0be0524 (поймана при написании этого теста):
    generation.py использовал ContextBuilder в рантайме, но импортировал его
    только под TYPE_CHECKING → NameError на builder-пути каждого запроса
    (production wiring main.py:47,83 всегда передаёт context_builder).
    Импорт исправлен; ниже — постоянный guard от повторения.
    """
    chat_id = uuid.uuid4()
    old_user = _history_message("user", "старый вопрос")
    old_assistant = _history_message("assistant", "старый ответ")
    prev_user = _history_message("user", "предпоследнее сообщение истории")
    current_message = _history_message("user", f"{_CURRENT_MARKER}: вопрос сейчас")
    _FakeChatSummaryRepository.summary_row = SimpleNamespace(
        summary={"conversation_summary": "ранняя сводка разговора"},
        covered_until_message_id=old_user.id,
    )
    monkeypatch.setattr(generation_module, "ChatSummaryRepository", _FakeChatSummaryRepository)
    # Guard P0: ContextBuilder обязан существовать в рантайме модуля
    # (TYPE_CHECKING-only импорт = NameError на builder-пути в production).
    assert hasattr(generation_module, "ContextBuilder"), (
        "generation.ContextBuilder не импортирован в рантайме "
        "(TYPE_CHECKING-only import — P0 NameError на builder-пути)"
    )

    # list_all возвращает историю, ВКЛЮЧАЯ только что добавленное user-сообщение
    # (имитация реального поведения add_message → list_all в той же сессии).
    messages_repo = _FakeMessagesRepo([old_user, old_assistant, prev_user, current_message])
    service = GenerationService(
        session_factory=None,
        registry=default_registry(),
        llm_stream=_empty_stream,
        settings=_SETTINGS,
        generation_registry=GenerationRegistry(),
        context_builder=ContextBuilder(TokenBudgetManager()),
    )
    bot = _RecordingBot()
    current_parts = [{"type": "text", "text": f"{_CURRENT_MARKER}: вопрос сейчас"}]

    result = await service._build_context(
        _FakeSession(),
        bot,
        tg_chat_id=42,
        chat_id=chat_id,
        model_def=default_registry().get("gemini-3.8-flash"),
        history=[old_user, old_assistant, prev_user],  # list_recent-хвост ДО current
        messages_repo=messages_repo,
        base_system_prompt="sys",
        memories=[],
        current_parts=current_parts,
        tool_defs=[],
        effective_settings=_SETTINGS,
        exclude_message_id=current_message.id,
    )

    assert result is not None
    llm_messages = result[0]
    # Путь со сводкой действительно выбран (list_all вызван для этого чата).
    assert messages_repo.list_all_calls == [chat_id]
    # (a) current встречается в user-сообщениях запроса ровно ОДИН раз.
    assert _marker_occurrences(llm_messages, _CURRENT_MARKER) == 1
    # (b) предпоследнее сообщение истории (не current) присутствует в контексте.
    assert any(
        part.get("text") == "предпоследнее сообщение истории"
        for part in _parts_of(llm_messages, "user", "text")
    )
    # Сводка отрендерена в system prompt (путь builder+summary действительно активен).
    assert "Сводка предыдущего разговора" in result[1]
    # Отказа «контекст слишком большой» не было (fits=True → бот молчит).
    assert bot.calls == []
