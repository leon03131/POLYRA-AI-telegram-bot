"""Фоновая compaction: сжатие старой истории чата в JSON-сводку.

Raw history в PostgreSQL НЕ удаляется и НЕ заменяется — сводка живёт
отдельной записью chat_summaries и лишь помечает покрытый префикс
(covered_until_message_id). Самые свежие keep_recent сообщений никогда
не компактятся. Запрос к внутренней модели (gemini-3.5-flash-lite) —
строгий JSON, один repair при невалидном ответе (ADR-006).
"""

from __future__ import annotations

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


def _normalize_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Привести распарсенный JSON к канонической форме сводки."""

    def _str_list(key: str) -> list[str]:
        raw = data.get(key)
        if not isinstance(raw, list):
            return []
        return [str(item) for item in raw if item]

    summary_text = data.get("conversation_summary")
    result: dict[str, Any] = {
        "conversation_summary": summary_text.strip() if isinstance(summary_text, str) else "",
    }
    for key in _SUMMARY_LIST_KEYS:
        result[key] = _str_list(key)
    return result


def _message_text(message: Message) -> str:
    """Склеенный текст сообщения (только text parts)."""
    return " ".join(part.text for part in message.parts if part.type == "text" and part.text)


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
        """Обновить сводку, если непокрытый сегмент ≥ min_segment. True — обновлена."""
        state = await self._summaries.load(chat_id)
        messages = await self._messages.list_all(chat_id)
        segment, covered_count = self._segment(messages, state)
        if len(segment) < self._min_segment:
            return False

        prompt = self._build_prompt(state.summary if state else None, segment)
        data = await self._ask_json(prompt)
        if data is None:
            logger.warning("compaction: модель не вернула валидный JSON (chat %s)", chat_id)
            return False

        await self._summaries.save(
            chat_id,
            summary=_normalize_summary(data),
            covered_until_message_id=segment[-1].id,
            covered_messages_count=covered_count,
        )
        return True

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
        for message in segment:
            text = _message_text(message)
            if text:
                lines.append(f"{message.role}: {text}")
        return "\n".join(lines)

    async def _ask_json(self, prompt: str) -> dict[str, Any] | None:
        """Запрос к summary-модели; при невалидном JSON — ровно один repair."""
        text = await self._request_text(prompt)
        data = extract_json_object(text)
        if data is not None:
            return data
        repair_prompt = (
            "Твой предыдущий ответ не удалось распарсить как JSON. "
            "Верни ТОЛЬКО валидный JSON-объект, без markdown и пояснений.\n\n"
            f"Предыдущий ответ:\n{text}"
        )
        repaired = await self._request_text(repair_prompt)
        return extract_json_object(repaired)

    async def _request_text(self, prompt: str) -> str:
        request = LLMRequest(
            model=self._summary_model,
            messages=[{"role": "user", "parts": [{"type": "text", "text": prompt}]}],
            thinking=self._summary_thinking,
            max_output_tokens=2048,
        )
        return await collect_text(self._llm_stream, request)
