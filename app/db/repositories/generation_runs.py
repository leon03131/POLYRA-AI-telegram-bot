"""Репозиторий запусков генерации."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GenerationRun


class GenerationRunRepository:
    """Операции над GenerationRun. Commit/rollback — уровень сервисов/uow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **fields: Any) -> GenerationRun:
        """Создать запуск генерации с указанными полями."""
        run = GenerationRun(**fields)
        self._session.add(run)
        await self._session.flush()
        return run

    async def get(self, run_id: uuid.UUID) -> GenerationRun | None:
        """Найти запуск по UUID."""
        return await self._session.get(GenerationRun, run_id)

    async def finish(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        first_token_at: datetime | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        tool_calls_count: int | None = None,
        error_category: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """Зафиксировать финал запуска: status + finished_at + переданные метрики.

        None-поля не перезаписываются (None = «нет данных», а не «обнулить»).
        """
        run = await self.get(run_id)
        if run is None:
            return
        run.status = status
        run.finished_at = datetime.now(UTC)
        if first_token_at is not None:
            run.first_token_at = first_token_at
        if input_tokens is not None:
            run.input_tokens = input_tokens
        if output_tokens is not None:
            run.output_tokens = output_tokens
        if reasoning_tokens is not None:
            run.reasoning_tokens = reasoning_tokens
        if tool_calls_count is not None:
            run.tool_calls_count = tool_calls_count
        if error_category is not None:
            run.error_category = error_category
        if error_code is not None:
            run.error_code = error_code
        await self._session.flush()
