"""Репозиторий запусков генерации."""

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GenerationRun


def _utc_today_start() -> datetime:
    """Полночь текущего UTC-дня (граница «сегодня» для admin-метрик)."""
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


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
        attempts: list[str] | None = None,
        gemini_project_id: uuid.UUID | None = None,
    ) -> None:
        """Зафиксировать финал запуска: status + finished_at + переданные метрики.

        None-поля не перезаписываются (None = «нет данных», а не «обнулить»).
        """
        run = await self.get(run_id)
        if run is None:
            return
        run.status = status
        run.finished_at = datetime.now(UTC)
        self._apply_metrics(
            run,
            first_token_at=first_token_at,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            tool_calls_count=tool_calls_count,
        )
        self._apply_outcome_fields(
            run,
            error_category=error_category,
            error_code=error_code,
            attempts=attempts,
            gemini_project_id=gemini_project_id,
        )
        await self._session.flush()

    @staticmethod
    def _apply_metrics(
        run: GenerationRun,
        *,
        first_token_at: datetime | None,
        input_tokens: int | None,
        output_tokens: int | None,
        reasoning_tokens: int | None,
        tool_calls_count: int | None,
    ) -> None:
        """Метрики времени/токенов (None = не трогать)."""
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

    @staticmethod
    def _apply_outcome_fields(
        run: GenerationRun,
        *,
        error_category: str | None,
        error_code: str | None,
        attempts: list[str] | None,
        gemini_project_id: uuid.UUID | None,
    ) -> None:
        """Поля результата/ротации (None = не трогать)."""
        if error_category is not None:
            run.error_category = error_category
        if error_code is not None:
            run.error_code = error_code
        if attempts is not None:
            run.attempts = attempts
        if gemini_project_id is not None:
            run.gemini_project_id = gemini_project_id

    async def count_since(self, user_id: uuid.UUID, *, since: datetime) -> int:
        """Число запусков пользователя с момента `since` (для requests/day)."""
        stmt = (
            select(func.count())
            .select_from(GenerationRun)
            .where(GenerationRun.user_id == user_id, GenerationRun.started_at >= since)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def abort_stale(self) -> int:
        """A34: при старте процесса все queued/running runs предыдущего процесса → aborted.

        Возвращает число затронутых запусков."""
        stmt = (
            update(GenerationRun)
            .where(GenerationRun.status.in_(("queued", "running")))
            .values(status="aborted", finished_at=datetime.now(UTC))
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        await self._session.flush()
        return int(result.rowcount or 0)

    async def tokens_since(self, user_id: uuid.UUID, *, since: datetime) -> int:
        """Сумма input+output токенов пользователя с момента `since` (для token limit)."""
        total_tokens = func.coalesce(GenerationRun.input_tokens, 0) + func.coalesce(
            GenerationRun.output_tokens, 0
        )
        stmt = select(func.coalesce(func.sum(total_tokens), 0)).where(
            GenerationRun.user_id == user_id, GenerationRun.started_at >= since
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def list_recent_failed(self, limit: int = 10) -> list[GenerationRun]:
        """Последние failed/aborted запуски, свежие первыми (admin stats, A35). Только чтение."""
        stmt = (
            select(GenerationRun)
            .where(GenerationRun.status.in_(("failed", "aborted")))
            .order_by(GenerationRun.started_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def avg_ttft_today(self) -> float | None:
        """Средний TTFT (first_token_at − started_at, сек) за сегодня (UTC) по completed (A28).

        Округлён до 1 знака; None, если сегодня нет completed-запусков с first_token_at.
        """
        ttft_s = func.extract("epoch", GenerationRun.first_token_at - GenerationRun.started_at)
        stmt = select(func.avg(ttft_s)).where(
            GenerationRun.started_at >= _utc_today_start(),
            GenerationRun.status == "completed",
            GenerationRun.first_token_at.is_not(None),
        )
        result = await self._session.execute(stmt)
        value = result.scalar_one()
        return None if value is None else round(float(value), 1)

    async def count_today_by_status(self) -> dict[str, int]:
        """Число запусков за сегодня (UTC) в разрезе статуса: {status: count}."""
        stmt = (
            select(GenerationRun.status, func.count())
            .where(GenerationRun.started_at >= _utc_today_start())
            .group_by(GenerationRun.status)
        )
        result = await self._session.execute(stmt)
        return {status: int(count) for status, count in result.all()}
