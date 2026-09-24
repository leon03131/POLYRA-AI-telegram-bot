"""Репозитории пула Gemini-проектов и учёта квот."""

import uuid
from datetime import UTC, date, datetime
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GeminiProject, QuotaDailyUsage, QuotaMinuteUsage, QuotaPolicy


class GeminiProjectRepository:
    """Операции над GeminiProject. Commit/rollback — уровень сервисов/uow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[GeminiProject]:
        """Все проекты в порядке ротации (rotation_order asc)."""
        stmt = select(GeminiProject).order_by(GeminiProject.rotation_order)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get(self, project_id: uuid.UUID) -> GeminiProject | None:
        """Найти проект по UUID."""
        return await self._session.get(GeminiProject, project_id)

    async def add(self, name: str, encrypted_api_key: str, key_hint: str) -> GeminiProject:
        """Добавить проект в конец ротации (rotation_order = max + 1)."""
        stmt = select(func.coalesce(func.max(GeminiProject.rotation_order), -1))
        result = await self._session.execute(stmt)
        project = GeminiProject(
            name=name,
            encrypted_api_key=encrypted_api_key,
            key_hint=key_hint,
            rotation_order=result.scalar_one() + 1,
        )
        self._session.add(project)
        await self._session.flush()
        return project

    async def set_enabled(self, project_id: uuid.UUID, enabled: bool) -> GeminiProject | None:
        """Включить/выключить проект; None, если не найден."""
        project = await self.get(project_id)
        if project is None:
            return None
        project.enabled = enabled
        await self._session.flush()
        return project

    async def move(self, project_id: uuid.UUID, direction: int) -> None:
        """Обменять rotation_order с соседом: direction +1 — со следующим, -1 — с предыдущим."""
        if direction == 0:
            return
        project = await self.get(project_id)
        if project is None:
            return
        if direction > 0:
            neighbor_stmt = (
                select(GeminiProject)
                .where(GeminiProject.rotation_order > project.rotation_order)
                .order_by(GeminiProject.rotation_order)
                .limit(1)
            )
        else:
            neighbor_stmt = (
                select(GeminiProject)
                .where(GeminiProject.rotation_order < project.rotation_order)
                .order_by(GeminiProject.rotation_order.desc())
                .limit(1)
            )
        result = await self._session.execute(neighbor_stmt)
        neighbor = result.scalar_one_or_none()
        if neighbor is None:
            return
        project.rotation_order, neighbor.rotation_order = (
            neighbor.rotation_order,
            project.rotation_order,
        )
        await self._session.flush()

    async def set_health(
        self,
        project_id: uuid.UUID,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Обновить health_status.

        "healthy" → last_success_at=now(UTC) и очистка last_error_*;
        иначе → last_error_at=now(UTC), код/сообщение (обрезаны до 64/256).
        """
        project = await self.get(project_id)
        if project is None:
            return
        now = datetime.now(UTC)
        project.health_status = status
        if status == "healthy":
            project.last_success_at = now
            project.last_error_at = None
            project.last_error_code = None
            project.last_error_message = None
        else:
            project.last_error_at = now
            project.last_error_code = error_code[:64] if error_code is not None else None
            project.last_error_message = error_message[:256] if error_message is not None else None
        await self._session.flush()

    async def set_last_error(
        self,
        project_id: uuid.UUID,
        *,
        error_code: str | None,
        error_message: str,
    ) -> None:
        """Зафиксировать last_error_* БЕЗ смены health_status (транзиентные ошибки)."""
        project = await self.get(project_id)
        if project is None:
            return
        project.last_error_at = datetime.now(UTC)
        project.last_error_code = error_code[:64] if error_code is not None else None
        project.last_error_message = error_message[:256]
        await self._session.flush()

    async def set_cooldown(self, project_id: uuid.UUID, until: datetime | None) -> None:
        """Установить/снять cooldown (cooldown_until)."""
        project = await self.get(project_id)
        if project is None:
            return
        project.cooldown_until = until
        await self._session.flush()

    async def delete(self, project_id: uuid.UUID) -> bool:
        """Удалить проект; True, если запись существовала."""
        project = await self.get(project_id)
        if project is None:
            return False
        await self._session.delete(project)
        await self._session.flush()
        return True


class QuotaPolicyRepository:
    """Лимиты квот per-model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[QuotaPolicy]:
        """Все политики (по model_id)."""
        stmt = select(QuotaPolicy).order_by(QuotaPolicy.model_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_model(self, model_id: str) -> QuotaPolicy | None:
        """Политика для конкретной модели."""
        stmt = select(QuotaPolicy).where(QuotaPolicy.model_id == model_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        model_id: str,
        *,
        rpm: int | None,
        tpm: int | None,
        rpd: int | None,
    ) -> QuotaPolicy:
        """Создать или обновить лимиты модели."""
        policy = await self.get_for_model(model_id)
        if policy is None:
            policy = QuotaPolicy(model_id=model_id, rpm=rpm, tpm=tpm, rpd=rpd)
            self._session.add(policy)
        else:
            policy.rpm = rpm
            policy.tpm = tpm
            policy.rpd = rpd
        await self._session.flush()
        return policy

    async def delete(self, model_id: str) -> bool:
        """Удалить политику по model_id; True, если запись существовала."""
        policy = await self.get_for_model(model_id)
        if policy is None:
            return False
        await self._session.delete(policy)
        await self._session.flush()
        return True


class QuotaUsageRepository:
    """Учёт фактического потребления квот (минутные/дневные окна)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_minute_usage(
        self,
        project_id: uuid.UUID,
        model_id: str,
        minute_ts: datetime,
    ) -> tuple[int, int]:
        """(requests, tokens_in) за минуту; (0, 0) при отсутствии записи."""
        stmt = select(QuotaMinuteUsage.requests_count, QuotaMinuteUsage.tokens_in).where(
            QuotaMinuteUsage.project_id == project_id,
            QuotaMinuteUsage.model_id == model_id,
            QuotaMinuteUsage.minute_ts == minute_ts,
        )
        result = await self._session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return (0, 0)
        return (row.requests_count, row.tokens_in)

    async def get_daily_usage(
        self,
        project_id: uuid.UUID,
        model_id: str,
        day: date,
    ) -> tuple[int, int]:
        """(requests, tokens_in) за день; (0, 0) при отсутствии записи."""
        stmt = select(QuotaDailyUsage.requests_count, QuotaDailyUsage.tokens_in).where(
            QuotaDailyUsage.project_id == project_id,
            QuotaDailyUsage.model_id == model_id,
            QuotaDailyUsage.day == day,
        )
        result = await self._session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return (0, 0)
        return (row.requests_count, row.tokens_in)

    async def reserve(
        self,
        project_id: uuid.UUID,
        model_id: str,
        *,
        minute_ts: datetime,
        day: date,
    ) -> None:
        """Зарезервировать запрос: +1 requests_count в минутном и дневном окнах.

        INSERT ... ON CONFLICT DO UPDATE; строки создаются при отсутствии.
        """
        minute_stmt = pg_insert(QuotaMinuteUsage).values(
            project_id=project_id,
            model_id=model_id,
            minute_ts=minute_ts,
            requests_count=1,
            tokens_in=0,
        )
        minute_stmt = minute_stmt.on_conflict_do_update(
            constraint="uq_quota_minute_usage_project_id",
            set_={"requests_count": QuotaMinuteUsage.requests_count + 1},
        )
        daily_stmt = pg_insert(QuotaDailyUsage).values(
            project_id=project_id,
            model_id=model_id,
            day=day,
            requests_count=1,
            tokens_in=0,
        )
        daily_stmt = daily_stmt.on_conflict_do_update(
            constraint="uq_quota_daily_usage_project_id",
            set_={"requests_count": QuotaDailyUsage.requests_count + 1},
        )
        await self._session.execute(minute_stmt)
        await self._session.execute(daily_stmt)

    async def add_tokens(
        self,
        project_id: uuid.UUID,
        model_id: str,
        *,
        minute_ts: datetime,
        day: date,
        tokens_in: int,
    ) -> None:
        """Добавить tokens_in в минутное и дневное окна; строки создаются при отсутствии."""
        minute_stmt = pg_insert(QuotaMinuteUsage).values(
            project_id=project_id,
            model_id=model_id,
            minute_ts=minute_ts,
            requests_count=0,
            tokens_in=tokens_in,
        )
        minute_stmt = minute_stmt.on_conflict_do_update(
            constraint="uq_quota_minute_usage_project_id",
            set_={"tokens_in": QuotaMinuteUsage.tokens_in + tokens_in},
        )
        daily_stmt = pg_insert(QuotaDailyUsage).values(
            project_id=project_id,
            model_id=model_id,
            day=day,
            requests_count=0,
            tokens_in=tokens_in,
        )
        daily_stmt = daily_stmt.on_conflict_do_update(
            constraint="uq_quota_daily_usage_project_id",
            set_={"tokens_in": QuotaDailyUsage.tokens_in + tokens_in},
        )
        await self._session.execute(minute_stmt)
        await self._session.execute(daily_stmt)

    async def list_recent_minute(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Последние минутные окна с именем проекта (свежие первыми), admin-просмотр."""
        stmt = (
            select(
                GeminiProject.name,
                QuotaMinuteUsage.model_id,
                QuotaMinuteUsage.minute_ts,
                QuotaMinuteUsage.requests_count,
                QuotaMinuteUsage.tokens_in,
            )
            .join(GeminiProject, QuotaMinuteUsage.project_id == GeminiProject.id)
            .order_by(QuotaMinuteUsage.minute_ts.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [
            {
                "project_name": row.name,
                "model_id": row.model_id,
                "minute_ts": row.minute_ts,
                "requests_count": row.requests_count,
                "tokens_in": row.tokens_in,
            }
            for row in result.all()
        ]

    async def list_recent_daily(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Последние дневные окна с именем проекта (свежие первыми), admin-просмотр."""
        stmt = (
            select(
                GeminiProject.name,
                QuotaDailyUsage.model_id,
                QuotaDailyUsage.day,
                QuotaDailyUsage.requests_count,
                QuotaDailyUsage.tokens_in,
            )
            .join(GeminiProject, QuotaDailyUsage.project_id == GeminiProject.id)
            .order_by(QuotaDailyUsage.day.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [
            {
                "project_name": row.name,
                "model_id": row.model_id,
                "day": row.day,
                "requests_count": row.requests_count,
                "tokens_in": row.tokens_in,
            }
            for row in result.all()
        ]

    async def delete_all(self) -> tuple[int, int]:
        """Удалить все строки минутного и дневного учёта; вернуть (minute, daily)."""
        minute_result = cast(
            "CursorResult[Any]", await self._session.execute(delete(QuotaMinuteUsage))
        )
        daily_result = cast(
            "CursorResult[Any]", await self._session.execute(delete(QuotaDailyUsage))
        )
        await self._session.flush()
        return (int(minute_result.rowcount or 0), int(daily_result.rowcount or 0))
