"""Репозиторий сводок чатов (compaction state)."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatSummary


class ChatSummaryRepository:
    """Операции над ChatSummary. Commit/rollback — уровень сервисов/uow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_chat(self, chat_id: uuid.UUID) -> ChatSummary | None:
        """Сводка чата; None, если чат ещё не компактился."""
        stmt = select(ChatSummary).where(ChatSummary.chat_id == chat_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        chat_id: uuid.UUID,
        *,
        summary: dict[str, Any],
        covered_until_message_id: uuid.UUID,
        covered_messages_count: int,
    ) -> ChatSummary:
        """Создать или обновить сводку чата (одна запись на чат)."""
        row = await self.get_for_chat(chat_id)
        if row is None:
            row = ChatSummary(
                chat_id=chat_id,
                summary=summary,
                covered_until_message_id=covered_until_message_id,
                covered_messages_count=covered_messages_count,
            )
            self._session.add(row)
        else:
            row.summary = summary
            row.covered_until_message_id = covered_until_message_id
            row.covered_messages_count = covered_messages_count
        await self._session.flush()
        return row
