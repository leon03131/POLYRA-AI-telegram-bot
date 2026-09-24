"""DB-адаптеры для GeminiProjectPool: ProjectStore/QuotaStore поверх репозиториев.

Каждый метод открывает короткую сессию из session_factory — пул живёт дольше,
чем request-scoped сессия бота. check_and_reserve_atomic — единственное место
с несколькими операциями в одной сессии: check+reserve обязаны быть одной
транзакцией (A09), иначе два параллельных запроса пройдут при лимите.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, datetime

from sqlalchemy import and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import QuotaDailyUsage, QuotaMinuteUsage
from app.db.repositories import GeminiProjectRepository, QuotaPolicyRepository, QuotaUsageRepository
from app.llm.gemini.pool import GeminiProjectPool, ProjectInfo
from app.llm.gemini.quota import QuotaLimits, QuotaTracker, UsageSnapshot
from app.security.crypto import CryptoBox


class DbProjectStore:
    """ProjectStore-протокол поверх GeminiProjectRepository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_all(self) -> list[ProjectInfo]:
        async with self._session_factory() as session:
            projects = await GeminiProjectRepository(session).list_all()
            return [
                ProjectInfo(
                    id=p.id,
                    name=p.name,
                    enabled=p.enabled,
                    cooldown_until=p.cooldown_until,
                    encrypted_api_key=p.encrypted_api_key,
                )
                for p in projects
            ]

    async def set_cooldown(self, project_id: uuid.UUID, until: datetime | None) -> None:
        async with self._session_factory() as session:
            await GeminiProjectRepository(session).set_cooldown(project_id, until)
            await session.commit()

    async def mark_unhealthy(
        self, project_id: uuid.UUID, *, error_code: str | None, error_message: str
    ) -> None:
        async with self._session_factory() as session:
            repo = GeminiProjectRepository(session)
            await repo.set_enabled(project_id, False)
            await repo.set_health(
                project_id, "unhealthy", error_code=error_code, error_message=error_message
            )
            await session.commit()

    async def mark_error(
        self, project_id: uuid.UUID, *, error_code: str | None, error_message: str
    ) -> None:
        async with self._session_factory() as session:
            await GeminiProjectRepository(session).set_last_error(
                project_id, error_code=error_code, error_message=error_message
            )
            await session.commit()

    async def mark_success(self, project_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            await GeminiProjectRepository(session).set_health(project_id, "healthy")
            await session.commit()


class DbQuotaStore:
    """QuotaStore-протокол поверх QuotaUsageRepository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_minute_usage(
        self, project_id: uuid.UUID, model_id: str, minute_ts: datetime
    ) -> UsageSnapshot:
        async with self._session_factory() as session:
            requests, tokens = await QuotaUsageRepository(session).get_minute_usage(
                project_id, model_id, minute_ts
            )
            return UsageSnapshot(requests=requests, tokens_in=tokens)

    async def get_daily_usage(
        self, project_id: uuid.UUID, model_id: str, day: date
    ) -> UsageSnapshot:
        async with self._session_factory() as session:
            requests, tokens = await QuotaUsageRepository(session).get_daily_usage(
                project_id, model_id, day
            )
            return UsageSnapshot(requests=requests, tokens_in=tokens)

    async def reserve(
        self, project_id: uuid.UUID, model_id: str, *, minute_ts: datetime, day: date
    ) -> None:
        async with self._session_factory() as session:
            await QuotaUsageRepository(session).reserve(
                project_id, model_id, minute_ts=minute_ts, day=day
            )
            await session.commit()

    async def add_tokens(
        self,
        project_id: uuid.UUID,
        model_id: str,
        *,
        minute_ts: datetime,
        day: date,
        tokens_in: int,
    ) -> None:
        async with self._session_factory() as session:
            await QuotaUsageRepository(session).add_tokens(
                project_id, model_id, minute_ts=minute_ts, day=day, tokens_in=tokens_in
            )
            await session.commit()

    async def check_and_reserve_atomic(
        self,
        project_id: uuid.UUID,
        model_id: str,
        *,
        minute_ts: datetime,
        day: date,
        limits: QuotaLimits,
    ) -> bool:
        """Check+reserve одной транзакцией (A09).

        Каждое окно — INSERT ... ON CONFLICT DO UPDATE с WHERE по лимитам:
        существующая строка инкрементируется, только если лимит не достигнут
        (конкурентные транзакции сериализуются row-lock'ом ON CONFLICT);
        отсутствующая строка вставляется (свежее окно всегда в пределах
        положительного лимита). RETURNING пуст при непрошедшем WHERE → отказ.
        Отказ любого окна → rollback обоих инкрементов.

        Лимит None не ограничивает. Лимит <= 0 — запрет без записи (INSERT
        свежего окна обходит WHERE, поэтому такие лимиты отсекаются заранее).
        TPM сравнивается с уже учтёнными tokens_in: фактические токены запроса
        неизвестны на момент reserve и довносятся reconcile post-factum —
        допуск сверх TPM после факта НЕ блокируется (local accounting,
        зафиксировано в .agents/reports/fix-v2/polyra-gemini/wave2.md).
        """
        if (
            (limits.rpm is not None and limits.rpm <= 0)
            or (limits.tpm is not None and limits.tpm <= 0)
            or (limits.rpd is not None and limits.rpd <= 0)
        ):
            return False
        async with self._session_factory() as session:
            minute_ok = await self._reserve_minute_window(
                session, project_id, model_id, minute_ts, limits
            )
            daily_ok = (
                await self._reserve_daily_window(session, project_id, model_id, day, limits)
                if minute_ok
                else False
            )
            if minute_ok and daily_ok:
                await session.commit()
                return True
            await session.rollback()
            return False

    @staticmethod
    async def _reserve_minute_window(
        session: AsyncSession,
        project_id: uuid.UUID,
        model_id: str,
        minute_ts: datetime,
        limits: QuotaLimits,
    ) -> bool:
        """+1 requests_count в минутном окне, если rpm/tpm не достигнуты."""
        conditions = []
        if limits.rpm is not None:
            conditions.append(QuotaMinuteUsage.requests_count < limits.rpm)
        if limits.tpm is not None:
            conditions.append(QuotaMinuteUsage.tokens_in < limits.tpm)
        insert_stmt = pg_insert(QuotaMinuteUsage).values(
            project_id=project_id,
            model_id=model_id,
            minute_ts=minute_ts,
            requests_count=1,
            tokens_in=0,
        )
        upsert_stmt = insert_stmt.on_conflict_do_update(
            constraint="uq_quota_minute_usage_project_id",
            set_={"requests_count": QuotaMinuteUsage.requests_count + 1},
            where=and_(*conditions) if conditions else None,
        ).returning(QuotaMinuteUsage.id)
        result = await session.execute(upsert_stmt)
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def _reserve_daily_window(
        session: AsyncSession,
        project_id: uuid.UUID,
        model_id: str,
        day: date,
        limits: QuotaLimits,
    ) -> bool:
        """+1 requests_count в суточном окне, если rpd не достигнут."""
        conditions = []
        if limits.rpd is not None:
            conditions.append(QuotaDailyUsage.requests_count < limits.rpd)
        insert_stmt = pg_insert(QuotaDailyUsage).values(
            project_id=project_id,
            model_id=model_id,
            day=day,
            requests_count=1,
            tokens_in=0,
        )
        upsert_stmt = insert_stmt.on_conflict_do_update(
            constraint="uq_quota_daily_usage_project_id",
            set_={"requests_count": QuotaDailyUsage.requests_count + 1},
            where=and_(*conditions) if conditions else None,
        ).returning(QuotaDailyUsage.id)
        result = await session.execute(upsert_stmt)
        return result.scalar_one_or_none() is not None


def build_gemini_pool(
    session_factory: async_sessionmaker[AsyncSession],
    crypto: CryptoBox,
) -> GeminiProjectPool:
    """Собрать GeminiProjectPool на PostgreSQL-backed stores.

    Лимиты читаются из quota_policies на каждый acquire (admin может менять
    их в Mini App без рестарта). Отсутствие политики = unlimited local accounting.
    """
    store = DbProjectStore(session_factory)

    async def quota_for_model(model_id: str) -> QuotaTracker:
        async with session_factory() as session:
            policy = await QuotaPolicyRepository(session).get_for_model(model_id)
        limits = (
            QuotaLimits(rpm=policy.rpm, tpm=policy.tpm, rpd=policy.rpd)
            if policy is not None
            else QuotaLimits()
        )
        return QuotaTracker(DbQuotaStore(session_factory), limits)

    decrypt: Callable[[str], str] = crypto.decrypt
    pool = GeminiProjectPool(
        store=store,
        quota_for_model=quota_for_model,
        decrypt=decrypt,
    )
    return pool
