"""Извлечение долговременной памяти из диалога (фон, после ответа ассистента).

Внутренняя модель (memory_model) возвращает строгий JSON со списком кандидатов;
repair не делается — это фоновая задача, невалидный ответ просто пропускаем.
Любые ошибки LLM/БД логируются и дают 0 — extract никогда не роняет ответ.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.context.compactor import collect_text, extract_json_object
from app.db.repositories import MemoryRepository
from app.llm.base import LLMRequest
from app.memory.deduplicator import find_duplicate
from app.memory.normalizer import normalize_memory_text

if TYPE_CHECKING:
    from app.db.models import Memory
    from app.services.llm_factory import LLMStreamFn

logger = logging.getLogger(__name__)

_MAX_SOURCE_CHARS = 2000
_MAX_CANDIDATES = 10
_EXISTING_SCAN_LIMIT = 200
_VALID_CATEGORIES = frozenset({"preference", "project", "fact", "instruction", "other"})

# Плейсхолдеры __USER__/__ASSISTANT__ подставляются через .replace() —
# str.format несовместим с литеральными {} в JSON-примере промпта.
MEMORY_EXTRACTION_PROMPT = """\
Ты — модуль долговременной памяти Telegram-бота. Проанализируй фрагмент диалога \
и извлеки ТОЛЬКО долговременно полезные факты о пользователе: стабильные \
предпочтения, проекты, договорённости, устойчивые факты, инструкции на будущее.

НЕ извлекай: одноразовые вопросы, поисковые запросы, промежуточный мусор, \
переформулировки без новой информации.

Ответ — СТРОГО один JSON-объект, без markdown и пояснений:
{"memories": [{"text": "...", "category": "preference|project|fact|instruction|other", \
"importance": 1-10}]}
Если нечего запомнить — {"memories": []}.

Пользователь: __USER__
Ассистент: __ASSISTANT__"""


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """Валидированный кандидат из ответа модели."""

    text: str
    category: str
    importance: int


def parse_candidates(data: dict[str, Any] | None) -> list[MemoryCandidate]:
    """Распарсенный JSON → валидные кандидаты (максимум _MAX_CANDIDATES)."""
    if not data:
        return []
    raw = data.get("memories")
    if not isinstance(raw, list):
        return []
    candidates: list[MemoryCandidate] = []
    for item in raw[:_MAX_CANDIDATES]:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        category = item.get("category")
        if category not in _VALID_CATEGORIES:
            category = "other"
        try:
            importance = int(item.get("importance", 5))
        except (TypeError, ValueError):
            importance = 5
        candidates.append(
            MemoryCandidate(
                text=text.strip(),
                category=str(category),
                importance=min(10, max(1, importance)),
            )
        )
    return candidates


class MemoryStore(Protocol):
    """Хранилище памяти для экстрактора. Изоляция: все вызовы с user_id."""

    async def list_for_user(self, user_id: uuid.UUID, *, limit: int) -> list[Memory]:
        """Существующие записи пользователя (для дедупликации)."""
        ...

    async def add(
        self,
        user_id: uuid.UUID,
        *,
        text: str,
        normalized_text: str,
        category: str,
        importance: int,
        source_chat_id: uuid.UUID | None = None,
        source_message_id: uuid.UUID | None = None,
    ) -> Memory:
        """Сохранить новую запись."""
        ...

    async def update_fields(
        self, memory_id: uuid.UUID, user_id: uuid.UUID, **fields: Any
    ) -> Memory | None:
        """Обновить поля записи (None — не найдена или чужая)."""
        ...

    async def find_by_normalized(self, user_id: uuid.UUID, normalized_text: str) -> Memory | None:
        """Точное совпадение по нормализованному тексту."""
        ...


class DbMemoryStore:
    """MemoryStore поверх MemoryRepository (сессия и commit на операцию)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_for_user(self, user_id: uuid.UUID, *, limit: int) -> list[Memory]:
        async with self._session_factory() as session:
            return await MemoryRepository(session).list_for_user(user_id, limit=limit)

    async def add(
        self,
        user_id: uuid.UUID,
        *,
        text: str,
        normalized_text: str,
        category: str,
        importance: int,
        source_chat_id: uuid.UUID | None = None,
        source_message_id: uuid.UUID | None = None,
    ) -> Memory:
        async with self._session_factory() as session:
            memory = await MemoryRepository(session).add(
                user_id,
                text=text,
                normalized_text=normalized_text,
                category=category,
                importance=importance,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
            )
            await session.commit()
            return memory

    async def update_fields(
        self, memory_id: uuid.UUID, user_id: uuid.UUID, **fields: Any
    ) -> Memory | None:
        async with self._session_factory() as session:
            memory = await MemoryRepository(session).update_fields(memory_id, user_id, **fields)
            await session.commit()
            return memory

    async def find_by_normalized(self, user_id: uuid.UUID, normalized_text: str) -> Memory | None:
        async with self._session_factory() as session:
            return await MemoryRepository(session).find_by_normalized(user_id, normalized_text)


class MemoryExtractor:
    """Извлекает долговременные факты из обмена и складывает в MemoryStore."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        llm_stream: LLMStreamFn,
        memory_model: str,
        memory_thinking: str,
        min_chars: int = 200,
        dedup_threshold: float = 0.85,
        memory_store: MemoryStore | None = None,
    ) -> None:
        if memory_store is None:
            if session_factory is None:
                raise ValueError("нужен session_factory или memory_store")
            memory_store = DbMemoryStore(session_factory)
        self._store = memory_store
        self._llm_stream = llm_stream
        self._memory_model = memory_model
        self._memory_thinking = memory_thinking
        self._min_chars = min_chars
        self._dedup_threshold = dedup_threshold

    async def extract_and_store(
        self,
        *,
        user_id: uuid.UUID,
        chat_id: uuid.UUID,
        user_text: str,
        assistant_text: str,
    ) -> int:
        """Извлечь и сохранить факты из обмена. Возвращает число НОВЫХ записей.

        Фоновая задача: любые ошибки логируются, метод не падает (→ 0).
        """
        if len(user_text) + len(assistant_text) < self._min_chars:
            return 0
        prompt = MEMORY_EXTRACTION_PROMPT.replace("__USER__", user_text[:_MAX_SOURCE_CHARS])
        prompt = prompt.replace("__ASSISTANT__", assistant_text[:_MAX_SOURCE_CHARS])
        request = LLMRequest(
            model=self._memory_model,
            messages=[{"role": "user", "parts": [{"type": "text", "text": prompt}]}],
            thinking=self._memory_thinking,
            max_output_tokens=1024,
        )
        try:
            data = extract_json_object(await collect_text(self._llm_stream, request))
        except Exception as exc:
            logger.warning("memory extraction: LLM недоступна (user %s): %s", user_id, exc)
            return 0
        candidates = parse_candidates(data)
        if not candidates:
            return 0
        try:
            return await self._store_candidates(
                user_id=user_id, chat_id=chat_id, candidates=candidates
            )
        except Exception:
            logger.exception("memory extraction: ошибка БД (user %s)", user_id)
            return 0

    async def _store_candidates(
        self,
        *,
        user_id: uuid.UUID,
        chat_id: uuid.UUID,
        candidates: list[MemoryCandidate],
    ) -> int:
        existing = await self._store.list_for_user(user_id, limit=_EXISTING_SCAN_LIMIT)
        added = 0
        for candidate in candidates:
            normalized = normalize_memory_text(candidate.text)
            if not normalized:
                continue
            duplicate = find_duplicate(normalized, existing, threshold=self._dedup_threshold)
            if duplicate is not None:
                await self._merge_duplicate(duplicate, candidate, user_id=user_id, chat_id=chat_id)
                continue
            memory = await self._store.add(
                user_id,
                text=candidate.text,
                normalized_text=normalized,
                category=candidate.category,
                importance=candidate.importance,
                source_chat_id=chat_id,
            )
            existing.append(memory)  # дедупликация внутри самого batch
            added += 1
        return added

    async def _merge_duplicate(
        self,
        duplicate: Memory,
        candidate: MemoryCandidate,
        *,
        user_id: uuid.UUID,
        chat_id: uuid.UUID,
    ) -> None:
        """Дубликат: importance=max, source — свежий; text — только для near-dup.

        Exact normalized match не несёт новой информации → text не трогаем
        (иначе тривиальная пунктуация перезаписывала бы каноническую форму).
        """
        fields: dict[str, Any] = {
            "importance": max(duplicate.importance, candidate.importance),
            "source_chat_id": chat_id,
        }
        is_exact = normalize_memory_text(candidate.text) == duplicate.normalized_text
        if not is_exact and len(candidate.text) > len(duplicate.text):
            fields["text"] = candidate.text
            fields["normalized_text"] = normalize_memory_text(candidate.text)
        await self._store.update_fields(duplicate.id, user_id, **fields)
