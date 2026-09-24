"""Репозиторий долговременной памяти (memories)."""

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Memory


class MemoryRepository:
    """Операции над Memory. Commit/rollback — уровень сервисов/uow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int = 100, offset: int = 0
    ) -> list[Memory]:
        """Память пользователя: важность desc, затем свежесть обновления desc."""
        stmt = (
            select(Memory)
            .where(Memory.user_id == user_id)
            .order_by(Memory.importance.desc(), Memory.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        """Число записей памяти пользователя (для пагинации)."""
        stmt = select(func.count()).select_from(Memory).where(Memory.user_id == user_id)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def list_all(self, *, limit: int = 50, offset: int = 0) -> list[Memory]:
        """Admin: память всех пользователей (свежие обновления первыми)."""
        stmt = select(Memory).order_by(Memory.updated_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_all(self) -> int:
        """Admin: всего записей памяти (для пагинации)."""
        stmt = select(func.count()).select_from(Memory)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

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
        """Создать запись памяти."""
        memory = Memory(
            user_id=user_id,
            text=text,
            normalized_text=normalized_text,
            category=category,
            importance=importance,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
        )
        self._session.add(memory)
        await self._session.flush()
        return memory

    async def update_fields(
        self, memory_id: uuid.UUID, user_id: uuid.UUID, **fields: Any
    ) -> Memory | None:
        """Обновить поля записи; None — не найдена или чужая (user_id обязателен)."""
        memory = await self._session.get(Memory, memory_id)
        if memory is None or memory.user_id != user_id:
            return None
        for field_name, value in fields.items():
            setattr(memory, field_name, value)
        await self._session.flush()
        return memory

    async def delete(self, memory_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Удалить свою запись; False — не найдена или чужая."""
        memory = await self._session.get(Memory, memory_id)
        if memory is None or memory.user_id != user_id:
            return False
        await self._session.delete(memory)
        await self._session.flush()
        return True

    async def find_by_normalized(self, user_id: uuid.UUID, normalized_text: str) -> Memory | None:
        """Первая запись с точным совпадением нормализованного текста."""
        stmt = select(Memory).where(
            Memory.user_id == user_id,
            Memory.normalized_text == normalized_text,
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def search_fts(self, user_id: uuid.UUID, query: str, *, limit: int = 5) -> list[Memory]:
        """PostgreSQL FTS по text (конфиг simple — языко-нейтральный).

        Порядок: importance DESC, затем last_used_at DESC NULLS LAST.
        """
        stmt = (
            select(Memory)
            .where(
                Memory.user_id == user_id,
                func.to_tsvector("simple", Memory.text).op("@@")(
                    func.plainto_tsquery("simple", query)
                ),
            )
            .order_by(Memory.importance.desc(), Memory.last_used_at.desc().nulls_last())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def touch_used(self, memory_ids: list[uuid.UUID]) -> None:
        """Отметить использование: last_used_at = now() (bulk update)."""
        if not memory_ids:
            return
        stmt = update(Memory).where(Memory.id.in_(memory_ids)).values(last_used_at=func.now())
        await self._session.execute(stmt)
        await self._session.flush()
