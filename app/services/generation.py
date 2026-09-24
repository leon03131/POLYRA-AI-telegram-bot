"""Сервис генерации ответов: полный цикл от входящего сообщения до ответа модели.

Одна активная генерация на чат (GenerationRegistry), отмена — по
(tg_chat_id, draft_id) через asyncio.Event, который уважают провайдеры.
Стрим — в Telegram-драфт (DraftStreamer), финал — персистентное сообщение.
Персистенс: user/assistant messages + generation_runs (usage, статус, ошибка).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.streaming.draft import MESSAGE_LIMIT, DraftStreamer, new_draft_id
from app.config import Settings
from app.db.repositories import (
    ChatRepository,
    ChatSummaryRepository,
    GenerationRunRepository,
    MessageRepository,
    UserSettingsRepository,
)
from app.llm.base import LLMRequest, LLMTool
from app.llm.errors import (
    AuthError,
    InvalidRequestError,
    NetworkError,
    ProviderError,
    RateLimitError,
    SafetyError,
    ServerError,
    TimeoutError_,
)
from app.llm.events import Done, LLMEvent, ReasoningDelta, TextDelta, ToolCall, Usage
from app.llm.gemini.pool import PoolExhaustedError
from app.llm.registry import ModelRegistry, UnknownModelError
from app.llm.tools.registry import ToolContext, ToolRegistry
from app.llm.tools.runner import ToolExecution, ToolRunner
from app.llm.tools.schemas import make_llm_tools
from app.services.access import EffectivePermissions, is_model_allowed
from app.services.chats import ChatService
from app.services.llm_factory import LLMStreamFn

if TYPE_CHECKING:
    from app.context import ContextBuilder, ContextCompactor, TitleGenerator
    from app.db.models import Chat, Message, User, UserSettings
    from app.memory import MemoryExtractor, PostgresFtsRetriever

logger = logging.getLogger(__name__)

_BUSY_MESSAGE = "⏳ Дождитесь завершения текущего ответа."
_MODEL_DENIED_MESSAGE = "⛔ Модель недоступна на вашем доступе."


@dataclass(slots=True)
class ActiveGeneration:
    """Активная генерация: задача, флаг отмены и корреляция с Telegram-драфтом."""

    task: asyncio.Task[None]
    cancellation: asyncio.Event
    draft_id: int
    tg_chat_id: int
    chat_id: uuid.UUID
    user_id: uuid.UUID | None = None


class GenerationRegistry:
    """Реестр активных генераций. Одна активная на чат; stop по (tg_chat_id, draft_id)."""

    def __init__(self) -> None:
        self._by_draft: dict[tuple[int, int], ActiveGeneration] = {}

    def register(self, gen: ActiveGeneration) -> None:
        """Зарегистрировать генерацию (ключ — (tg_chat_id, draft_id))."""
        self._by_draft[(gen.tg_chat_id, gen.draft_id)] = gen

    def pop(self, tg_chat_id: int, draft_id: int) -> ActiveGeneration | None:
        """Снять генерацию с учёта; None, если не найдена."""
        return self._by_draft.pop((tg_chat_id, draft_id), None)

    def find_active_for_chat(self, chat_id: uuid.UUID) -> ActiveGeneration | None:
        """Найти активную генерацию чата; None, если чат свободен."""
        for gen in self._by_draft.values():
            if gen.chat_id == chat_id:
                return gen
        return None

    def find_by_draft(self, tg_chat_id: int, draft_id: int) -> ActiveGeneration | None:
        """Найти генерацию по паре (tg_chat_id, draft_id)."""
        return self._by_draft.get((tg_chat_id, draft_id))

    def count_active_for_user(self, user_id: uuid.UUID) -> int:
        """Число активных генераций пользователя (для max_concurrent_generations)."""
        return sum(1 for gen in self._by_draft.values() if gen.user_id == user_id)

    async def stop(self, tg_chat_id: int, draft_id: int) -> bool:
        """Запросить отмену (выставить cancellation); True, если генерация найдена."""
        gen = self.find_by_draft(tg_chat_id, draft_id)
        if gen is None:
            return False
        gen.cancellation.set()
        return True


@dataclass(slots=True)
class StreamOutcome:
    """Итог потребления LLM-стрима (_consume)."""

    text: str
    usage: Usage | None
    cancelled: bool
    tool_calls_count: int
    first_token_at: datetime | None
    reasoning_chunks: int
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None


@dataclass(slots=True)
class _PreparedGeneration:
    """Контекст подготовленной генерации (после проверок и записи user-сообщения)."""

    chat_id: uuid.UUID
    run_id: uuid.UUID
    draft_id: int
    model_id: str
    model_display: str
    provider: str
    thinking: str | None
    system_prompt: str
    messages: list[dict[str, Any]]
    needs_compaction: bool = False
    chat_title: str | None = None
    user_text: str = ""
    user_id: uuid.UUID | None = None
    memory_extraction_enabled: bool = False
    web_enabled: bool = False


def user_error_message(exc: BaseException, model_display: str) -> str:
    """Текст ошибки для пользователя: по-русски, без stack trace и внутренностей."""
    if isinstance(exc, PoolExhaustedError):
        return (
            f"Все Gemini projects для модели {model_display} сейчас недоступны. Попробуйте позже."
        )
    if isinstance(exc, RateLimitError):
        return "Провайдер временно ограничил запросы (429). Попробуйте позже."
    if isinstance(exc, AuthError):
        return "Ошибка авторизации провайдера (ключ не настроен или отклонён)."
    if isinstance(exc, SafetyError):
        return "Запрос отклонён модерацией провайдера."
    if isinstance(exc, InvalidRequestError):
        return "Запрос некорректен для этой модели."
    if isinstance(exc, TimeoutError_ | NetworkError | ServerError):
        return "Провайдер временно недоступен. Можно повторить запрос."
    return "Произошла внутренняя ошибка. Попробуйте ещё раз позже."


def resolve_model_and_thinking(
    *,
    chat: Chat,
    user_settings: UserSettings,
    settings: Settings,
    registry: ModelRegistry,
) -> tuple[str, str | None]:
    """Модель и thinking для запроса: chat override → user default → global default.

    Неизвестная model_id → fallback на settings.default_model. Thinking вне
    thinking_modes модели → None (дефолт провайдера).
    """
    model_id = chat.model_id or user_settings.default_model_id or settings.default_model
    try:
        model_def = registry.get(model_id)
    except UnknownModelError:
        model_id = settings.default_model
        model_def = registry.get(model_id)
    thinking = chat.thinking_setting or user_settings.default_thinking or model_def.default_thinking
    if thinking is not None and thinking not in model_def.thinking_modes:
        thinking = None
    return model_id, thinking


_SOURCES_LINE = "SOURCES:"


def extract_sources(tool_content: str) -> list[tuple[str, str]]:
    """Пары (title, url) из результата web_search: нумерованный список «N. title\\nURL».

    Строка-маркер SOURCES: url1 | url2 — запасной источник url без заголовков.
    """
    sources: list[tuple[str, str]] = []
    lines = tool_content.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(_SOURCES_LINE):
            for url in stripped[len(_SOURCES_LINE) :].split("|"):
                url = url.strip()
                if url:
                    sources.append((url, url))
        elif (
            stripped
            and stripped[0].isdigit()
            and ". " in stripped[:5]
            and i + 1 < len(lines)
            and lines[i + 1].strip().startswith(("http://", "https://"))
        ):
            sources.append((stripped.split(". ", 1)[1].strip(), lines[i + 1].strip()))
    return sources


def collect_sources(tool_records: list[tuple[ToolCall, ToolExecution]]) -> list[tuple[str, str]]:
    """Все источники из web_search-вызовов, dedupe по url (порядок сохраняется)."""
    seen: set[str] = set()
    sources: list[tuple[str, str]] = []
    for call, execution in tool_records:
        if call.name != "web_search" or execution.result.is_error:
            continue
        for title, url in extract_sources(execution.result.content):
            if url not in seen:
                seen.add(url)
                sources.append((title, url))
    return sources


def build_sources_suffix(sources: list[tuple[str, str]]) -> str:
    """Markdown-блок «Источники» для финального сообщения."""
    lines = ["\n\n**Источники:**"]
    for i, (title, url) in enumerate(sources, 1):
        lines.append(f"{i}. [{title}]({url})")
    return "\n".join(lines)


def extract_user_text(parts: list[dict[str, Any]]) -> str:
    """Склеенный текст text-parts текущего сообщения (для title/compaction)."""
    return " ".join(
        str(part.get("text") or "") for part in parts if part.get("type") == "text"
    ).strip()


def build_messages(
    *, history: list[Message], current_parts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """История (ASC) + текущее сообщение → нормализованные messages для LLMRequest.

    Из истории берутся только text-пarts (bytes изображений не храним — только
    текущее сообщение может нести image с data_base64). Сообщения истории без
    текста пропускаются; current_parts передаются как есть.
    """
    messages: list[dict[str, Any]] = []
    for message in history:
        parts = [
            {"type": "text", "text": part.text or ""}
            for part in message.parts
            if part.type == "text"
        ]
        if parts:
            messages.append({"role": message.role, "parts": parts})
    messages.append({"role": "user", "parts": current_parts})
    return messages


class GenerationService:
    """Оркестратор генерации: проверки → запись → стрим в драфт → персистенс."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        registry: ModelRegistry,
        llm_stream: LLMStreamFn,
        settings: Settings,
        generation_registry: GenerationRegistry,
        context_builder: ContextBuilder | None = None,
        compactor: ContextCompactor | None = None,
        title_generator: TitleGenerator | None = None,
        memory_retriever: PostgresFtsRetriever | None = None,
        memory_extractor: MemoryExtractor | None = None,
        tool_registry: ToolRegistry | None = None,
        search_manager: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._llm_stream = llm_stream
        self._settings = settings
        self._generations = generation_registry
        self._context_builder = context_builder
        self._compactor = compactor
        self._title_generator = title_generator
        self._memory_retriever = memory_retriever
        self._memory_extractor = memory_extractor
        self._tool_registry = tool_registry
        self._search_manager = search_manager

    async def generate(
        self,
        *,
        bot: Bot,
        tg_chat_id: int,
        user: User,
        permissions: EffectivePermissions,
        current_parts: list[dict[str, Any]],
        model_hint_supports_image: bool = False,
    ) -> None:
        """Полный цикл ответа. Ошибки провайдеров не поднимаются — пользователю
        уходит текст через DraftStreamer.fail, запуск фиксируется failed.

        model_hint_supports_image — необязательная подсказка роутера «в сообщении
        есть изображение»; наличие image-part в current_parts проверяется всегда.
        """
        has_image = model_hint_supports_image or any(
            part.get("type") == "image" for part in current_parts
        )
        prepared = await self._prepare(
            bot=bot,
            tg_chat_id=tg_chat_id,
            user=user,
            permissions=permissions,
            current_parts=current_parts,
            has_image=has_image,
        )
        if prepared is None:
            return

        streamer = DraftStreamer(bot, tg_chat_id, prepared.draft_id)
        cancellation = asyncio.Event()
        task = asyncio.current_task()
        assert task is not None  # generate() вызывается из aiogram task-хендлера
        self._generations.register(
            ActiveGeneration(
                task=task,
                cancellation=cancellation,
                draft_id=prepared.draft_id,
                tg_chat_id=tg_chat_id,
                chat_id=prepared.chat_id,
                user_id=user.id,
            )
        )
        try:
            await streamer.flush(force=True)  # стартовый плейсхолдер-драфт
            llm_tools, tool_runner = self._prepare_tools(prepared, user, permissions)
            loop_result = await self._stream_loop(
                prepared,
                streamer,
                llm_tools=llm_tools,
                tool_runner=tool_runner,
                user=user,
                permissions=permissions,
                cancellation=cancellation,
            )
            if loop_result is None:
                return  # ошибка уже обработана внутри (fail + save_failed)
            final_outcome, tool_records = loop_result
            if final_outcome.cancelled:
                await self._save_cancelled(prepared, final_outcome, bot=bot, tg_chat_id=tg_chat_id)
            elif not final_outcome.text.strip():
                # Модель завершилась без видимого текста (напр. весь вывод ушёл в
                # мысли или потерянный tool call) — пользователь не должен молча
                # ждать: честное сообщение вместо пустого ответа.
                logger.warning(
                    "empty final text (run %s, model %s)", prepared.run_id, prepared.model_id
                )
                await streamer.fail(
                    f"🤔 {prepared.model_display} вернула пустой ответ. Попробуйте ещё раз."
                )
                await self._save_failed(prepared, ValueError("empty final text"))
            else:
                sources = collect_sources(tool_records) if self._settings.show_sources else []
                if sources and "Источники" not in final_outcome.text:
                    suffix = build_sources_suffix(sources)
                    await streamer.append(suffix)
                    final_outcome.text += suffix
                await streamer.finalize()
                await self._save_completed(prepared, final_outcome, tool_records)
                self._schedule_maintenance(prepared, final_outcome)
        finally:
            self._generations.pop(tg_chat_id, prepared.draft_id)

    def _prepare_tools(
        self,
        prepared: _PreparedGeneration,
        user: User,
        permissions: EffectivePermissions,
    ) -> tuple[list[LLMTool] | None, ToolRunner | None]:
        """Собрать инструменты для запроса: права + web off + title-tool только без title."""
        if self._tool_registry is None:
            return None, None
        enabled = self._tool_registry.list_enabled(permissions)
        if not prepared.web_enabled:
            enabled = [t for t in enabled if t.required_permission != "web_search"]
        if prepared.chat_title is not None:
            # set_chat_title доступен только пока title IS NULL
            enabled = [t for t in enabled if t.name != "set_chat_title"]
        if not enabled:
            return None, None
        runner = ToolRunner(self._tool_registry, session_factory=self._session_factory)
        return make_llm_tools(enabled), runner

    async def _stream_loop(
        self,
        prepared: _PreparedGeneration,
        streamer: DraftStreamer,
        *,
        llm_tools: list[LLMTool] | None,
        tool_runner: ToolRunner | None,
        user: User,
        permissions: EffectivePermissions,
        cancellation: asyncio.Event,
    ) -> tuple[StreamOutcome, list[tuple[ToolCall, ToolExecution]]] | None:
        """Цикл «стрим → tool calls → стрим». None — ошибка (уже обработана)."""
        messages = list(prepared.messages)
        text_parts: list[str] = []
        usage: Usage | None = None
        first_token_at: datetime | None = None
        reasoning_chunks = 0
        tool_records: list[tuple[ToolCall, ToolExecution]] = []
        cancelled = False
        iterations = 0

        while True:
            request = LLMRequest(
                model=prepared.model_id,
                messages=messages,
                system_prompt=prepared.system_prompt,
                thinking=prepared.thinking,
                tools=llm_tools,
                cancellation=cancellation,
            )
            try:
                outcome = await self._consume(self._llm_stream(request), streamer, cancellation)
            except (ProviderError, PoolExhaustedError) as exc:
                logger.info("generation failed (run %s): %s", prepared.run_id, exc)
                await streamer.fail(user_error_message(exc, prepared.model_display))
                await self._save_failed(prepared, exc)
                return None
            except Exception as exc:
                logger.exception("generation crashed (run %s)", prepared.run_id)
                await streamer.fail(user_error_message(exc, prepared.model_display))
                await self._save_failed(prepared, exc)
                return None

            if outcome.usage is not None:
                usage = outcome.usage
            if outcome.first_token_at is not None and first_token_at is None:
                first_token_at = outcome.first_token_at
            reasoning_chunks += outcome.reasoning_chunks
            text_parts.append(outcome.text)
            if outcome.cancelled:
                cancelled = True
                break
            if not self._should_run_tools(outcome, tool_runner, iterations):
                break
            iterations += 1
            assert tool_runner is not None  # гарантировано _should_run_tools
            tool_context = ToolContext(
                user_id=user.id,
                chat_id=prepared.chat_id,
                permissions=permissions,
                session_factory=self._session_factory,
                settings=self._settings,
                search_manager=self._search_manager,
            )
            messages.append(self._assistant_tool_message(outcome.tool_calls))
            for call in outcome.tool_calls:
                execution = await tool_runner.execute(
                    call, tool_context, generation_run_id=prepared.run_id
                )
                tool_records.append((call, execution))
                messages.append(
                    {
                        "role": "tool",
                        "parts": [
                            {
                                "type": "tool_result",
                                "call_id": call.id,
                                "name": call.name,
                                "content": execution.result.content,
                                "is_error": execution.result.is_error,
                            }
                        ],
                    }
                )
            if cancellation.is_set():
                cancelled = True
                break

        final = StreamOutcome(
            text="".join(text_parts),
            usage=usage,
            cancelled=cancelled,
            tool_calls_count=len(tool_records),
            first_token_at=first_token_at,
            reasoning_chunks=reasoning_chunks,
        )
        return final, tool_records

    def _should_run_tools(
        self,
        outcome: StreamOutcome,
        tool_runner: ToolRunner | None,
        iterations: int,
    ) -> bool:
        """Продолжать ли tool loop.

        Триггер — НАЛИЧИЕ tool_calls, а не finish_reason: Gemini в стриме шлёт
        functionCall с finishReason=STOP (у него нет отдельной причины), OpenAI-
        совместимые модели шлют finish_reason="tool_calls". Оба случая — по
        факту наличия вызовов.
        """
        if not outcome.tool_calls:
            return False
        if tool_runner is None:
            logger.warning("модель запросила tools при отключённом tool engine")
            return False
        if iterations >= self._settings.max_tool_iterations:
            logger.warning("max tool iterations (%s) reached", self._settings.max_tool_iterations)
            return False
        return True

    @staticmethod
    def _assistant_tool_message(tool_calls: list[ToolCall]) -> dict[str, Any]:
        """Assistant-сообщение с tool_call parts (provider_meta — для thought signatures)."""
        return {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "id": call.id,
                    "name": call.name,
                    "arguments_json": call.arguments_json,
                    "provider_meta": call.provider_meta,
                }
                for call in tool_calls
            ],
        }

    def _schedule_maintenance(self, prepared: _PreparedGeneration, outcome: StreamOutcome) -> None:
        """Фон после успешного ответа: compaction, автоназвание, память."""
        if self._compactor is not None and prepared.needs_compaction:
            self._spawn_background(
                self._compactor.maybe_compact(prepared.chat_id), label="compaction"
            )
        if (
            self._title_generator is not None
            and prepared.chat_title is None
            and prepared.user_text
            and outcome.text
        ):
            self._spawn_background(
                self._title_generator.generate_and_set(
                    prepared.chat_id, prepared.user_text, outcome.text
                ),
                label="title",
            )
        if (
            self._memory_extractor is not None
            and prepared.memory_extraction_enabled
            and prepared.user_id is not None
            and prepared.user_text
            and outcome.text
        ):
            self._spawn_background(
                self._memory_extractor.extract_and_store(
                    user_id=prepared.user_id,
                    chat_id=prepared.chat_id,
                    user_text=prepared.user_text,
                    assistant_text=outcome.text,
                ),
                label="memory_extraction",
            )

    @staticmethod
    def _spawn_background(coro: Coroutine[Any, Any, Any], *, label: str) -> None:
        """Запустить фоновую задачу; ошибки — только в лог (никогда не роняют ответ)."""
        task = asyncio.create_task(coro)

        def _log_error(done: asyncio.Task[Any]) -> None:
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                logger.warning("фоновая задача %s завершилась ошибкой: %s", label, exc)

        task.add_done_callback(_log_error)

    async def _consume(
        self,
        stream: AsyncIterator[LLMEvent],
        streamer: DraftStreamer,
        cancellation: asyncio.Event,
    ) -> StreamOutcome:
        """Потребить события стрима в streamer; вернуть итог (текст, usage, флаги).

        ReasoningDelta не покидает сервис (только счётчик); ToolCall в M5 —
        warning (tools отключены). Отмена (event/CancelledError) — не ошибка:
        цикл прекращается, накопленный partial возвращается с cancelled=True.
        """
        text_parts: list[str] = []
        usage: Usage | None = None
        first_token_at: datetime | None = None
        tool_calls_count = 0
        reasoning_chunks = 0
        cancelled = False
        tool_calls: list[ToolCall] = []
        finish_reason: str | None = None
        try:
            async for event in stream:
                if cancellation.is_set():
                    cancelled = True
                    break
                if isinstance(event, TextDelta):
                    if first_token_at is None:
                        first_token_at = datetime.now(UTC)
                    text_parts.append(event.text)
                    await streamer.append(event.text)
                elif isinstance(event, ReasoningDelta):
                    reasoning_chunks += 1
                elif isinstance(event, ToolCall):
                    tool_calls_count += 1
                    tool_calls.append(event)
                elif isinstance(event, Usage):
                    usage = event
                elif isinstance(event, Done):
                    finish_reason = event.finish_reason
                    break
        except asyncio.CancelledError:
            cancelled = True
        # Провайдер мог тихо завершить генератор по cancellation (без Done).
        cancelled = cancelled or cancellation.is_set()
        return StreamOutcome(
            text="".join(text_parts),
            usage=usage,
            cancelled=cancelled,
            tool_calls_count=tool_calls_count,
            first_token_at=first_token_at,
            reasoning_chunks=reasoning_chunks,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )

    async def _check_user_limits(
        self,
        session: AsyncSession,
        bot: Bot,
        tg_chat_id: int,
        user: User,
        permissions: EffectivePermissions,
    ) -> bool:
        """Проверить per-user лимиты гранта. True — отказ уже отправлен пользователю."""
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        runs_repo = GenerationRunRepository(session)
        if permissions.requests_per_day is not None:
            today_count = await runs_repo.count_since(user.id, since=day_start)
            if today_count >= permissions.requests_per_day:
                await session.commit()
                await bot.send_message(
                    tg_chat_id, "⛔ Дневной лимит запросов исчерпан. Попробуйте завтра."
                )
                return True
        if permissions.token_limit is not None:
            tokens_today = await runs_repo.tokens_since(user.id, since=day_start)
            if tokens_today >= permissions.token_limit:
                await session.commit()
                await bot.send_message(
                    tg_chat_id, "⛔ Дневной лимит токенов исчерпан. Попробуйте завтра."
                )
                return True
        if self._generations.count_active_for_user(user.id) >= max(
            permissions.max_concurrent_generations, 1
        ):
            await session.commit()
            await bot.send_message(tg_chat_id, _BUSY_MESSAGE)
            return True
        return False

    async def _prepare(
        self,
        *,
        bot: Bot,
        tg_chat_id: int,
        user: User,
        permissions: EffectivePermissions,
        current_parts: list[dict[str, Any]],
        has_image: bool,
    ) -> _PreparedGeneration | None:
        """Проверки + запись user-сообщения и запуска (commit); None — ответили, стоп."""
        async with self._session_factory() as session:
            chat_service = ChatService(session)
            chat = await chat_service.get_or_create_current_chat(user.id)
            chat_id = chat.id
            if self._generations.find_active_for_chat(chat_id) is not None:
                await bot.send_message(tg_chat_id, _BUSY_MESSAGE)
                return None

            messages_repo = MessageRepository(session)
            # История — ДО текущего сообщения (current_parts передаются отдельно);
            # берём с запасом x2: без builder режем ниже, с builder он сам выберет хвост.
            history = await messages_repo.list_recent(
                chat_id, limit=self._settings.recent_history_limit * 2
            )
            await messages_repo.add_message(chat_id, "user", parts=current_parts)
            await ChatRepository(session).touch(chat_id)

            user_settings = await UserSettingsRepository(session).get_or_create(user.id)
            model_id, thinking = resolve_model_and_thinking(
                chat=chat,
                user_settings=user_settings,
                settings=self._settings,
                registry=self._registry,
            )
            model_def = self._registry.get(model_id)
            if not is_model_allowed(permissions, model_id):
                await session.commit()
                await bot.send_message(tg_chat_id, _MODEL_DENIED_MESSAGE)
                return None
            if has_image and not model_def.supports_images:
                image_models = [
                    m.display_name
                    for m in self._registry.filter_by_permissions(permissions.allowed_models)
                    if m.supports_images
                ]
                await session.commit()
                await bot.send_message(
                    tg_chat_id,
                    f"⛔ Модель {model_def.display_name} не принимает изображения. "
                    f"Модели с поддержкой изображений: "
                    f"{', '.join(image_models) if image_models else 'нет доступных'}.",
                )
                return None

            # Per-user лимиты гранта (server-side; скрытая кнопка ≠ защита)
            limit_denial = await self._check_user_limits(
                session, bot, tg_chat_id, user, permissions
            )
            if limit_denial:
                return None

            user_text = extract_user_text(current_parts)
            # chat override → user default; права доступа важнее обеих настроек
            memory_enabled = bool(permissions.can_use_memory) and (
                chat.memory_enabled
                if chat.memory_enabled is not None
                else user_settings.memory_enabled
            )
            web_mode = chat.web_mode if chat.web_mode is not None else user_settings.web_mode
            web_enabled = bool(permissions.can_use_web_search) and web_mode != "off"
            memories: list[str] = []
            if memory_enabled and self._memory_retriever is not None:
                try:
                    retrieved = await self._memory_retriever.retrieve(
                        user.id, user_text, limit=self._settings.memory_retrieval_limit
                    )
                    memories = [memory.text for memory in retrieved]
                except Exception:
                    logger.warning("memory retrieval failed (user %s)", user.id, exc_info=True)

            base_system_prompt = chat.system_prompt_override or self._settings.default_system_prompt
            chat_title = chat.title
            needs_compaction = False
            if self._context_builder is None:
                history = history[-self._settings.recent_history_limit :]
                llm_messages = build_messages(history=history, current_parts=current_parts)
                system_prompt = base_system_prompt
            else:
                summary_row = await ChatSummaryRepository(session).get_for_chat(chat_id)
                built = self._context_builder.build(
                    model=model_def,
                    base_system_prompt=base_system_prompt,
                    summary_json=summary_row.summary if summary_row is not None else None,
                    memories=memories,
                    history=history,
                )
                llm_messages = [*built.messages, {"role": "user", "parts": current_parts}]
                system_prompt = built.system_prompt
                needs_compaction = built.needs_compaction

            draft_id = new_draft_id()
            run = await GenerationRunRepository(session).create(
                chat_id=chat_id,
                user_id=user.id,
                provider=model_def.provider,
                model_id=model_id,
                thinking_setting=thinking,
                status="running",
                draft_id=draft_id,
            )
            await session.commit()
        return _PreparedGeneration(
            chat_id=chat_id,
            run_id=run.id,
            draft_id=draft_id,
            model_id=model_id,
            model_display=model_def.display_name,
            provider=model_def.provider,
            thinking=thinking,
            system_prompt=system_prompt,
            messages=llm_messages,
            needs_compaction=needs_compaction,
            chat_title=chat_title,
            user_text=user_text,
            user_id=user.id,
            memory_extraction_enabled=memory_enabled,
            web_enabled=web_enabled,
        )

    async def _save_completed(
        self,
        prepared: _PreparedGeneration,
        outcome: StreamOutcome,
        tool_records: list[tuple[ToolCall, ToolExecution]] | None = None,
    ) -> None:
        """Финал нормы: assistant message (done) + run completed + commit."""
        usage = outcome.usage
        parts: list[dict[str, Any]] = [{"type": "text", "text": outcome.text}]
        for call, execution in tool_records or []:
            parts.append(
                {
                    "type": "tool_call",
                    "text": f"{call.name}({call.arguments_json[:200]})",
                    "metadata_json": {"name": call.name, "arguments_json": call.arguments_json},
                }
            )
            parts.append(
                {
                    "type": "tool_result",
                    "text": execution.result.content[:500],
                    "metadata_json": {"name": call.name, "status": execution.status},
                }
            )
        try:
            async with self._session_factory() as session:
                await MessageRepository(session).add_message(
                    prepared.chat_id,
                    "assistant",
                    parts=parts,
                    status="done",
                    provider=prepared.provider,
                    model_id=prepared.model_id,
                    generation_run_id=prepared.run_id,
                    input_tokens=usage.input_tokens if usage else None,
                    output_tokens=usage.output_tokens if usage else None,
                    reasoning_tokens=usage.reasoning_tokens if usage else None,
                )
                await ChatRepository(session).touch(prepared.chat_id)
                await GenerationRunRepository(session).finish(
                    prepared.run_id,
                    status="completed",
                    first_token_at=outcome.first_token_at,
                    input_tokens=usage.input_tokens if usage else None,
                    output_tokens=usage.output_tokens if usage else None,
                    reasoning_tokens=usage.reasoning_tokens if usage else None,
                    tool_calls_count=outcome.tool_calls_count,
                )
                await session.commit()
        except IntegrityError:
            # Чат удалён в Mini App прямо во время генерации: FK на chats.
            # Ответ пользователь уже получил (finalize до сохранения) — просто лог.
            logger.warning(
                "chat %s удалён во время генерации — пропускаю персистенс", prepared.chat_id
            )

    async def _save_cancelled(
        self,
        prepared: _PreparedGeneration,
        outcome: StreamOutcome,
        *,
        bot: Bot,
        tg_chat_id: int,
    ) -> None:
        """Финал отмены: partial обычным сообщением + assistant message cancelled."""
        partial = outcome.text
        if partial:
            text = partial if len(partial) <= MESSAGE_LIMIT else partial[: MESSAGE_LIMIT - 1] + "…"
            try:
                await bot.send_message(tg_chat_id, text)
            except TelegramAPIError:
                logger.exception("не удалось отправить partial в чат %s", tg_chat_id)
        usage = outcome.usage
        try:
            async with self._session_factory() as session:
                await MessageRepository(session).add_message(
                    prepared.chat_id,
                    "assistant",
                    parts=[{"type": "text", "text": partial}],
                    status="cancelled",
                    provider=prepared.provider,
                    model_id=prepared.model_id,
                    generation_run_id=prepared.run_id,
                    input_tokens=usage.input_tokens if usage else None,
                    output_tokens=usage.output_tokens if usage else None,
                    reasoning_tokens=usage.reasoning_tokens if usage else None,
                )
                await ChatRepository(session).touch(prepared.chat_id)
                await GenerationRunRepository(session).finish(
                    prepared.run_id,
                    status="cancelled",
                    first_token_at=outcome.first_token_at,
                    tool_calls_count=outcome.tool_calls_count,
                )
                await session.commit()
        except IntegrityError:
            # Чат удалён во время генерации — partial уже отправлен, персистенс пропускаем.
            logger.warning(
                "chat %s удалён во время генерации — пропускаю персистенс", prepared.chat_id
            )

    async def _save_failed(self, prepared: _PreparedGeneration, exc: BaseException) -> None:
        """Финал ошибки: run failed (category/code); assistant message не создаётся."""
        if isinstance(exc, ProviderError):
            error_category: str | None = exc.category.value
            error_code = exc.raw_code
        elif isinstance(exc, PoolExhaustedError):
            error_category = "pool_exhausted"
            error_code = None
        else:
            error_category = "internal"
            error_code = type(exc).__name__
        async with self._session_factory() as session:
            await GenerationRunRepository(session).finish(
                prepared.run_id,
                status="failed",
                error_category=error_category,
                error_code=error_code,
            )
            await session.commit()
