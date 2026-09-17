"""Репозиторий чатов."""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chat


class ChatRepository:
    """Операции над Chat. Commit/rollback — уровень сервисов/uow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, owner_user_id: uuid.UUID, **fields: Any) -> Chat:
        """Создать чат с указанными полями."""
        chat = Chat(owner_user_id=owner_user_id, **fields)
        self._session.add(chat)
        await self._session.flush()
        return chat

    async def get(self, chat_id: uuid.UUID) -> Chat | None:
        """Найти чат по UUID."""
        return await self._session.get(Chat, chat_id)

    async def list_for_user(
        self,
        owner_user_id: uuid.UUID,
        *,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Chat]:
        """Список чатов пользователя (свежие первыми) с пагинацией."""
        stmt = select(Chat).where(Chat.owner_user_id == owner_user_id)
        if not include_archived:
            stmt = stmt.where(Chat.archived_at.is_(None))
        stmt = stmt.order_by(Chat.updated_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_active(self, owner_user_id: uuid.UUID) -> Chat | None:
        """Последний по updated_at неархивированный чат пользователя."""
        stmt = (
            select(Chat)
            .where(Chat.owner_user_id == owner_user_id, Chat.archived_at.is_(None))
            .order_by(Chat.updated_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def rename(self, chat_id: uuid.UUID, title: str) -> Chat | None:
        """Переименовать чат; None, если чат не найден."""
        chat = await self.get(chat_id)
        if chat is None:
            return None
        chat.title = title
        await self._session.flush()
        return chat

    async def set_archived(self, chat_id: uuid.UUID, archived: bool) -> Chat | None:
        """Установить/снять архивацию (archived_at = now()/NULL); None, если не найден."""
        chat = await self.get(chat_id)
        if chat is None:
            return None
        chat.archived_at = func.now() if archived else None
        await self._session.flush()
        return chat

    async def update_settings(self, chat_id: uuid.UUID, **fields: Any) -> Chat | None:
        """Обновить per-chat настройки (model_id, web_mode, ...); None, если не найден."""
        chat = await self.get(chat_id)
        if chat is None:
            return None
        for key, value in fields.items():
            setattr(chat, key, value)
        await self._session.flush()
        return chat

    async def delete(self, chat_id: uuid.UUID) -> bool:
        """Удалить чат; True, если запись существовала."""
        chat = await self.get(chat_id)
        if chat is None:
            return False
        await self._session.delete(chat)
        await self._session.flush()
        return True

    async def touch(self, chat_id: uuid.UUID) -> None:
        """Обновить updated_at чата (bump свежести)."""
        chat = await self.get(chat_id)
        if chat is None:
            return
        chat.updated_at = func.now()
        await self._session.flush()
