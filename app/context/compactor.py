"""Фоновая compaction: сжатие старой истории чата в JSON-сводку.

Raw history в PostgreSQL НЕ удаляется и НЕ заменяется — сводка живёт
отдельной записью chat_summaries и лишь помечает покрытый префикс
(covered_until_message_id). Самые свежие keep_recent сообщений никогда
не компактятся. Запрос к внутренней модели (gemini-3.5-flash-lite) —
строгий JSON, один repair при невалидном ответе (ADR-006).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repositories import ChatSummaryRepository, MessageRepository
from app.llm.base import LLMRequest
from app.llm.events import Done, ReasoningDelta, TextDelta

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.db.models import Message
    from app.llm.events import LLMEvent
    from app.services.llm_factory import LLMStreamFn

logger = logging.getLogger(__name__)

SUMMARY_JSON_SCHEMA_PROMPT = """\
Ты — модуль сжатия истории диалога Telegram-бота. Составь ОБНОВЛЁННУЮ сводку \
разговора, учитывая предыдущую сводку (если есть) и новый фрагмент диалога.

Ответ — СТРОГО один JSON-объект, без markdown и пояснений, со структурой:
{
  "conversation_summary": "связный пересказ разговора (3-8 предложений)",
  "important_facts": ["факт 1", "факт 2"],
  "decisions": ["принятое решение"],
  "open_threads": ["незакрытый вопрос или задача"],
  "user_preferences": ["предпочтение пользователя"],
  "entities": ["имена, проекты, технологии, ссылки"]
}

Правила:
- Не выдумывай факты: только то, что явно есть в диалоге или предыдущей сводке.
- Сохраняй числа, имена, даты, принятые решения и незакрытые задачи.
- Списки могут быть пустыми.
- Пиши на языке диалога."""

_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.DOTALL)

_SUMMARY_LIST_KEYS = (
    "important_facts",
    "decisions",
    "open_threads",
    "user_preferences",
    "entities",
)

# Сериализация compaction per chat_id: два compaction одного чата никогда
# не идут параллельно (fire-and-forget задачи переживают генерацию).
_COMPACTION_LOCKS: dict[str, asyncio.Lock] = {}

# Ограничение рендера диалога в prompt compaction: длинный сегмент режется
# по СЕРЕДИНЕ (голова и хвост сохраняются), с маркером пропуска.
_MAX_DIALOG_CHARS = 12_000
_ELISION_MARKER = "… [середина фрагмента пропущена: {count} сообщ.] …"


def _chat_lock(chat_id: uuid.UUID) -> asyncio.Lock:
    """Per-chat лок compaction (process-wide; ключ — строковый chat_id)."""
    return _COMPACTION_LOCKS.setdefault(str(chat_id), asyncio.Lock())


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Вытащить JSON-объект из ответа модели (```json fence или голый JSON)."""
    stripped = text.strip()
    if not stripped:
        return None
    match = _FENCE_RE.search(stripped)
    candidates = [match.group("body").strip()] if match else []
    candidates.append(stripped)
    for candidate in candidates:
        data = _try_loads(candidate)
        if data is None:
            start, end = candidate.find("{"), candidate.rfind("}")
            if 0 <= start < end:
                data = _try_loads(candidate[start : end + 1])
        if isinstance(data, dict):
            return data
    return None


def _try_loads(candidate: str) -> Any:
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None


def _normalize_summary(data: dict[str, Any]) -> dict[str, Any] | None:
    """Строгая валидация + каноническая форма сводки. None — невалидно.

    Отклоняются: пустой dict, отсутствие обязательных ключей, неправильные
    типы (conversation_summary — непустая строка; списки — list). Невалидная
    сводка НЕ должна продвигать covered_until — вызывающий код трактует None
    как неудачу compaction.
    """
    if not isinstance(data, dict) or not data:
        return None
    summary_text = data.get("conversation_summary")
    if not isinstance(summary_text, str) or not summary_text.strip():
        return None
    result: dict[str, Any] = {"conversation_summary": summary_text.strip()}
    for key in _SUMMARY_LIST_KEYS:
        raw = data.get(key)
        if not isinstance(raw, list):
            return None
        result[key] = [str(item) for item in raw if item]
    return result


def _message_text(message: Message) -> str:
    """Склеенный текст сообщения (только text parts)."""
    return " ".join(part.text for part in message.parts if part.type == "text" and part.text)


def _cap_dialog(dialog_lines: list[str]) -> list[str]:
    """Ограничить рендер диалога ~_MAX_DIALOG_CHARS, вырезая СЕРЕДИНУ с маркером.

    Голова и хвост сегмента сохраняются (каждому — до половины лимита);
    пропущенная середина помечается маркером с числом вырезанных сообщений.
    Хвост (самые свежие сообщения сегмента) не теряется никогда, пока
    отдельные строки короче половины лимита.
    """
    if sum(len(line) + 1 for line in dialog_lines) <= _MAX_DIALOG_CHARS:
        return dialog_lines
    half = _MAX_DIALOG_CHARS // 2
    head: list[str] = []
    used = 0
    for line in dialog_lines:
        if used + len(line) + 1 > half:
            break
        head.append(line)
        used += len(line) + 1
    tail: list[str] = []
    used = 0
    for line in reversed(dialog_lines):
        if used + len(line) + 1 > half:
            break
        tail.append(line)
        used += len(line) + 1
    tail.reverse()
    skipped = len(dialog_lines) - len(head) - len(tail)
    return [*head, _ELISION_MARKER.format(count=max(1, skipped)), *tail]


async def collect_text(llm_stream: LLMStreamFn, request: LLMRequest) -> str:
    """Собрать видимый текст стрима (ReasoningDelta игнорируется)."""
    parts: list[str] = []
    stream: AsyncIterator[LLMEvent] = llm_stream(request)
    async for event in stream:
        if isinstance(event, TextDelta):
            parts.append(event.text)
        elif isinstance(event, ReasoningDelta):
            continue
        elif isinstance(event, Done):
            break
    return "".join(parts)


@dataclass(frozen=True, slots=True)
class SummaryState:
    """Текущее состояние сводки чата (для SummaryStore)."""

    summary: dict[str, Any]
    covered_until_message_id: uuid.UUID | None


class SummaryStore(Protocol):
    """Источник/приёмник сводки чата."""

    async def load(self, chat_id: uuid.UUID) -> SummaryState | None:
        """Текущая сводка; None, если чат ещё не компактился."""
        ...

    async def save(
        self,
        chat_id: uuid.UUID,
        *,
        summary: dict[str, Any],
        covered_until_message_id: uuid.UUID,
        covered_messages_count: int,
    ) -> None:
        """Сохранить обновлённую сводку."""
        ...


class MessageStore(Protocol):
    """Источник полной истории чата."""

    async def list_all(self, chat_id: uuid.UUID) -> list[Message]:
        """Все сообщения чата, ASC."""
        ...


class DbSummaryStore:
    """SummaryStore поверх ChatSummaryRepository (сессия на операцию)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def load(self, chat_id: uuid.UUID) -> SummaryState | None:
        async with self._session_factory() as session:
            row = await ChatSummaryRepository(session).get_for_chat(chat_id)
            if row is None:
                return None
            return SummaryState(
                summary=dict(row.summary),
                covered_until_message_id=row.covered_until_message_id,
            )

    async def save(
        self,
        chat_id: uuid.UUID,
        *,
        summary: dict[str, Any],
        covered_until_message_id: uuid.UUID,
        covered_messages_count: int,
    ) -> None:
        async with self._session_factory() as session:
            await ChatSummaryRepository(session).upsert(
                chat_id,
                summary=summary,
                covered_until_message_id=covered_until_message_id,
                covered_messages_count=covered_messages_count,
            )
            await session.commit()


class DbMessageStore:
    """MessageStore поверх MessageRepository (сессия на операцию)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_all(self, chat_id: uuid.UUID) -> list[Message]:
        async with self._session_factory() as session:
            return await MessageRepository(session).list_all(chat_id)


class ContextCompactor:
    """Инкрементальное обновление сводки чата внутренней моделью."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        llm_stream: LLMStreamFn,
        summary_model: str,
        summary_thinking: str,
        keep_recent: int,
        min_segment: int,
        summary_store: SummaryStore | None = None,
        message_store: MessageStore | None = None,
    ) -> None:
        if summary_store is None or message_store is None:
            if session_factory is None:
                raise ValueError("нужен session_factory или оба store (summary/message)")
            summary_store = summary_store or DbSummaryStore(session_factory)
            message_store = message_store or DbMessageStore(session_factory)
        self._summaries = summary_store
        self._messages = message_store
        self._llm_stream = llm_stream
        self._summary_model = summary_model
        self._summary_thinking = summary_thinking
        self._keep_recent = keep_recent
        self._min_segment = min_segment

    async def maybe_compact(self, chat_id: uuid.UUID) -> bool:
        """Обновить сводку, если непокрытый сегмент ≥ min_segment. True — обновлена.

        Сериализуется per chat_id: повторный вызов ждёт завершения текущего.
        Невалидная/пустая сводка и откат boundary не сохраняются (False).
        """
        async with _chat_lock(chat_id):
            state = await self._summaries.load(chat_id)
            messages = await self._messages.list_all(chat_id)
            segment, covered_count = self._segment(messages, state)
            if len(segment) < self._min_segment:
                return False

            prompt = self._build_prompt(state.summary if state else None, segment)
            summary = await self._ask_json(prompt)
            if summary is None:
                logger.warning("compaction: модель не вернула валидную сводку (chat %s)", chat_id)
                return False

            new_covered_until = segment[-1].id
            # Monotonic guard: boundary не откатываем. Перечитываем состояние —
            # между snapshot и сохранением boundary мог продвинуть другой writer.
            fresh = await self._summaries.load(chat_id)
            if not self._boundary_is_forward(messages, fresh, new_covered_until):
                logger.warning(
                    "compaction: пропуск сохранения — boundary не продвигается (chat %s)",
                    chat_id,
                )
                return False

            await self._summaries.save(
                chat_id,
                summary=summary,
                covered_until_message_id=new_covered_until,
                covered_messages_count=covered_count,
            )
            return True

    @staticmethod
    def _boundary_is_forward(
        messages: list[Message],
        state: SummaryState | None,
        new_covered_until: uuid.UUID,
    ) -> bool:
        """True, если новый boundary СТРОГО позже текущего (по позициям в ASC-истории).

        Текущий covered_until, не найденный в истории (её чистили вручную),
        считается устаревшим — новый boundary валиден (безопасный пересчёт).
        """
        if state is None or state.covered_until_message_id is None:
            return True
        positions = {str(message.id): index for index, message in enumerate(messages)}
        current_index = positions.get(str(state.covered_until_message_id))
        if current_index is None:
            return True
        new_index = positions.get(str(new_covered_until))
        if new_index is None:
            return False  # новый boundary обязан присутствовать в истории
        return new_index > current_index

    def _segment(
        self, messages: list[Message], state: SummaryState | None
    ) -> tuple[list[Message], int]:
        """Непокрытый сводкой сегмент за пределами keep_recent + его позиция конца."""
        end = max(0, len(messages) - self._keep_recent)
        start = 0
        if state is not None and state.covered_until_message_id is not None:
            for index, message in enumerate(messages):
                if message.id == state.covered_until_message_id:
                    start = index + 1
                    break
            # covered id не найден (history чистили вручную) → считаем всё непокрытым
        if end <= start:
            return [], 0
        return messages[start:end], end

    def _build_prompt(self, previous: dict[str, Any] | None, segment: list[Message]) -> str:
        lines = [SUMMARY_JSON_SCHEMA_PROMPT, ""]
        if previous:
            lines.append("Предыдущая сводка (обнови её, а не пиши с нуля):")
            lines.append(json.dumps(previous, ensure_ascii=False, indent=2))
            lines.append("")
        lines.append("Новый фрагмент диалога (в хронологическом порядке):")
        dialog_lines = []
        for message in segment:
            text = _message_text(message)
            if text:
                dialog_lines.append(f"{message.role}: {text}")
        lines.extend(_cap_dialog(dialog_lines))
        return "\n".join(lines)

    async def _ask_json(self, prompt: str) -> dict[str, Any] | None:
        """Запрос к summary-модели; невалидная сводка → ровно один repair.

        Возвращает каноническую (провалидированную) сводку или None.
        """
        text = await self._request_text(prompt)
        data = extract_json_object(text)
        if data is not None:
            summary = _normalize_summary(data)
            if summary is not None:
                return summary
        repair_prompt = (
            "Твой предыдущий ответ не удалось распарсить как валидную сводку JSON. "
            "Верни ТОЛЬКО валидный JSON-объект по заданной схеме, "
            "без markdown и пояснений.\n\n"
            f"Предыдущий ответ:\n{text}"
        )
        repaired = extract_json_object(await self._request_text(repair_prompt))
        if repaired is None:
            return None
        return _normalize_summary(repaired)

    async def _request_text(self, prompt: str) -> str:
        request = LLMRequest(
            model=self._summary_model,
            messages=[{"role": "user", "parts": [{"type": "text", "text": prompt}]}],
            thinking=self._summary_thinking,
            max_output_tokens=2048,
        )
        return await collect_text(self._llm_stream, request)
