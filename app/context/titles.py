"""Автоматические названия чатов (фон, после первого ответа ассистента)."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.context.compactor import collect_text, extract_json_object
from app.db.repositories import ChatRepository
from app.llm.base import LLMRequest

if TYPE_CHECKING:
    from app.services.llm_factory import LLMStreamFn

logger = logging.getLogger(__name__)

_QUOTES = "\"'«»„“”‚‘’`"
_MAX_SOURCE_CHARS = 1000

# Плейсхолдеры __USER__/__ASSISTANT__ подставляются через .replace() —
# str.format несовместим с литеральными {} в JSON-примере промпта.
_TITLE_PROMPT_TEMPLATE = """\
Придумай короткое название чата по фрагменту диалога.
Требования: 3-7 слов, без кавычек, по существу темы, на языке диалога.
Ответ — строго JSON вида {"title": "..."}, без пояснений.

Пользователь: __USER__
Ассистент: __ASSISTANT__"""


def sanitize_title(raw: str) -> str | None:
    """Нормализовать название: без кавычек/переводов строк, 3–7 слов, ≤ 60 символов."""
    text = " ".join(raw.split())  # переводы строк и повторные пробелы → один пробел
    text = text.strip(_QUOTES + " \t")
    if not text:
        return None
    words = text.split()
    if len(words) < 3:
        return None
    if len(words) > 7:
        text = " ".join(words[:7])
    if len(text) > 60:
        text = text[:60].rstrip()
    return text or None


class TitleGenerator:
    """Генерирует название чата внутренней моделью и пишет его в БД."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        llm_stream: LLMStreamFn,
        title_model: str,
        title_thinking: str,
    ) -> None:
        self._session_factory = session_factory
        self._llm_stream = llm_stream
        self._title_model = title_model
        self._title_thinking = title_thinking

    async def generate_and_set(
        self,
        chat_id: uuid.UUID,
        first_user_text: str,
        first_assistant_text: str,
    ) -> bool:
        """Сгенерировать и установить название. True — название установлено."""
        prompt = _TITLE_PROMPT_TEMPLATE.replace(
            "__USER__", first_user_text[:_MAX_SOURCE_CHARS]
        ).replace("__ASSISTANT__", first_assistant_text[:_MAX_SOURCE_CHARS])
        request = LLMRequest(
            model=self._title_model,
            messages=[{"role": "user", "parts": [{"type": "text", "text": prompt}]}],
            thinking=self._title_thinking,
            max_output_tokens=256,
        )
        data = extract_json_object(await collect_text(self._llm_stream, request))
        raw_title = data.get("title") if data else None
        title = sanitize_title(raw_title) if isinstance(raw_title, str) else None
        if title is None:
            logger.info("title: модель не вернула валидное название (chat %s)", chat_id)
            return False

        async with self._session_factory() as session:
            chats = ChatRepository(session)
            chat = await chats.get(chat_id)
            if chat is None or chat.title is not None:
                return False  # не перетираем существующее (в т.ч. ручное) название
            await chats.rename(chat_id, title)
            await session.commit()
        return True
