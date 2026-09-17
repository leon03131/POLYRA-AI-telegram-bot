"""Ретривер долговременной памяти.

Реализация — PostgreSQL FTS (конфиг simple, языко-нейтральный). Интерфейс
MemoryRetriever готов к semantic-ретриву на pgvector (будущее расширение).
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repositories import MemoryRepository

if TYPE_CHECKING:
    from app.db.models import Memory

logger = logging.getLogger(__name__)


class MemoryRetriever(Protocol):
    """Источник релевантных записей памяти пользователя."""

    async def retrieve(self, user_id: uuid.UUID, query_text: str, *, limit: int) -> list[Memory]:
        """До limit релевантных записей (может быть меньше или пусто)."""
        ...


class RetrievalStore(Protocol):
    """Минимальный контракт хранилища для retrieve_memories (тестируемо без БД)."""

    async def search_fts(self, user_id: uuid.UUID, query: str, *, limit: int) -> list[Memory]:
        """FTS-совпадения по тексту запроса."""
        ...

    async def list_for_user(self, user_id: uuid.UUID, *, limit: int) -> list[Memory]:
        """Топ важных/свежих записей (fallback)."""
        ...

    async def touch_used(self, memory_ids: list[uuid.UUID]) -> None:
        """Отметить использование записей (last_used_at)."""
        ...


async def retrieve_memories(
    store: RetrievalStore, user_id: uuid.UUID, query_text: str, *, limit: int
) -> list[Memory]:
    """FTS по запросу; пустой запрос/нет совпадений → fallback на топ важных.

    Использованные записи помечаются через touch_used (тот же store/сессия).
    """
    memories: list[Memory] = []
    if query_text.strip():
        memories = await store.search_fts(user_id, query_text, limit=limit)
    if not memories:
        memories = await store.list_for_user(user_id, limit=limit)
    if memories:
        await store.touch_used([memory.id for memory in memories])
    return memories


class _SessionStore:
    """RetrievalStore поверх MemoryRepository в рамках одной сессии."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = MemoryRepository(session)

    async def search_fts(self, user_id: uuid.UUID, query: str, *, limit: int) -> list[Memory]:
        return await self._repo.search_fts(user_id, query, limit=limit)

    async def list_for_user(self, user_id: uuid.UUID, *, limit: int) -> list[Memory]:
        return await self._repo.list_for_user(user_id, limit=limit)

    async def touch_used(self, memory_ids: list[uuid.UUID]) -> None:
        await self._repo.touch_used(memory_ids)


class PostgresFtsRetriever:
    """MemoryRetriever поверх PostgreSQL FTS; короткая сессия на вызов."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def retrieve(self, user_id: uuid.UUID, query_text: str, *, limit: int) -> list[Memory]:
        """Релевантные записи пользователя; использованные помечаются last_used_at."""
        async with self._session_factory() as session:
            memories = await retrieve_memories(
                _SessionStore(session), user_id, query_text, limit=limit
            )
            if memories:
                await session.commit()  # фиксируем touch_used
            return memories
