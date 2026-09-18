"""Репозиторий сообщений."""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message, MessagePart

_PART_COLUMNS = frozenset({"type", "text", "telegram_file_id", "mime_type", "metadata_json"})


class MessageRepository:
    """Операции над Message/MessagePart. Commit/rollback — уровень сервисов/uow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_message(
        self,
        chat_id: uuid.UUID,
        role: str,
        *,
        parts: list[dict[str, Any]] | None = None,
        **fields: Any,
    ) -> Message:
        """Создать сообщение и его части (position = индекс в списке parts).

        Ключи part, которых нет среди колонок MessagePart (например data_base64 —
        байты изображений в БД не храним), отбрасываются.
        """
        message = Message(chat_id=chat_id, role=role, **fields)
        for position, part in enumerate(parts or []):
            safe_part = {k: v for k, v in part.items() if k in _PART_COLUMNS}
            message.parts.append(MessagePart(position=position, **safe_part))
        self._session.add(message)
        await self._session.flush()
        return message

    async def get(self, message_id: uuid.UUID) -> Message | None:
        """Найти сообщение по UUID (parts подгружаются через selectin)."""
        return await self._session.get(Message, message_id)

    async def list_recent(self, chat_id: uuid.UUID, *, limit: int = 50) -> list[Message]:
        """Последние N сообщений чата в хронологическом порядке (ASC)."""
        stmt = (
            select(Message)
            .where(Message.chat_id == chat_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(reversed(result.scalars().all()))

    async def list_all(self, chat_id: uuid.UUID) -> list[Message]:
        """Все сообщения чата в хронологическом порядке (ASC). Для compaction."""
        stmt = select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at.asc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(self, message_id: uuid.UUID, status: str) -> None:
        """Обновить status сообщения (no-op, если сообщение не найдено)."""
        message = await self.get(message_id)
        if message is None:
            return
        message.status = status
        await self._session.flush()

    async def count_for_chat(self, chat_id: uuid.UUID) -> int:
        """Количество сообщений в чате."""
        stmt = select(func.count(Message.id)).where(Message.chat_id == chat_id)
        result = await self._session.execute(stmt)
        return result.scalar_one()
