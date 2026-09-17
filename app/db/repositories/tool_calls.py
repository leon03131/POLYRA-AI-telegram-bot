"""Репозиторий записей вызовов инструментов."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ToolCallRecord


class ToolCallRepository:
    """Аудит tool calls. Commit — уровень выше."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        generation_run_id: uuid.UUID,
        chat_id: uuid.UUID,
        tool_name: str,
        arguments_json: str,
        status: str,
        result_preview: str,
        duration_ms: int | None,
    ) -> ToolCallRecord:
        record = ToolCallRecord(
            generation_run_id=generation_run_id,
            chat_id=chat_id,
            tool_name=tool_name,
            arguments_json=arguments_json[:4000],
            status=status,
            result_preview=result_preview[:500],
            duration_ms=duration_ms,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def list_for_run(
        self, generation_run_id: uuid.UUID, *, limit: int = 100
    ) -> list[ToolCallRecord]:
        stmt = (
            select(ToolCallRecord)
            .where(ToolCallRecord.generation_run_id == generation_run_id)
            .order_by(ToolCallRecord.created_at)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
