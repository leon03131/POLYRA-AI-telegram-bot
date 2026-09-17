"""Учёт квот Gemini-проектов: RPM / TPM(input) / RPD.

Окна измерений (docs/vendor/GEMINI.md): минутные счётчики по UTC-минуте,
суточные — по дате в America/Los_Angeles (ресет RPD в полночь Pacific).
Хранилище спрятано за Protocol QuotaStore (реализация — PostgreSQL, вне
этого модуля), поэтому трекер полностью тестируем in-memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")


def current_minute(now: datetime) -> datetime:
    """Метка минутного окна: now в UTC, усечённое до минуты (second=microsecond=0)."""
    return now.astimezone(UTC).replace(second=0, microsecond=0)


def pacific_day(now: datetime) -> date:
    """Дата now в America/Los_Angeles — ключ суточной квоты (RPD)."""
    return now.astimezone(PACIFIC).date()


@dataclass(frozen=True, slots=True)
class QuotaLimits:
    """Лимиты проект+модель; None означает «не ограничивает»."""

    rpm: int | None = None
    tpm: int | None = None
    rpd: int | None = None


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """Потребление за одно окно (минута или сутки)."""

    requests: int = 0
    tokens_in: int = 0


class QuotaStore(Protocol):
    """Хранилище счётчиков квот (реализация — PostgreSQL, пишется отдельно)."""

    async def get_minute_usage(
        self, project_id: UUID, model_id: str, minute_ts: datetime
    ) -> UsageSnapshot: ...

    async def get_daily_usage(
        self, project_id: UUID, model_id: str, day: date
    ) -> UsageSnapshot: ...

    async def reserve(
        self, project_id: UUID, model_id: str, *, minute_ts: datetime, day: date
    ) -> None:
        """Атомарно зарезервировать один запрос в минутном и суточном окнах."""
        ...

    async def add_tokens(
        self,
        project_id: UUID,
        model_id: str,
        *,
        minute_ts: datetime,
        day: date,
        tokens_in: int,
    ) -> None:
        """Добавить фактические input-токены в минутное и суточное окна."""
        ...


class QuotaTracker:
    """Проверка лимитов с резервом запроса и reconcile фактических токенов."""

    def __init__(self, store: QuotaStore, limits: QuotaLimits) -> None:
        self._store = store
        self._limits = limits

    async def check_and_reserve(self, project_id: UUID, model_id: str, *, now: datetime) -> bool:
        """True — лимиты позволяют запрос и он зарезервирован; False — без reserve.

        Все лимиты None → reserve без чтения счётчиков. Иначе читаем минутное и
        суточное окна: rpm по minute.requests, tpm по minute.tokens_in, rpd по
        daily.requests; достигнут любой из заданных → False, счётчики не трогаем.
        """
        limits = self._limits
        minute_ts = current_minute(now)
        day = pacific_day(now)
        if limits.rpm is None and limits.tpm is None and limits.rpd is None:
            await self._store.reserve(project_id, model_id, minute_ts=minute_ts, day=day)
            return True

        minute = await self._store.get_minute_usage(project_id, model_id, minute_ts)
        daily = await self._store.get_daily_usage(project_id, model_id, day)
        if limits.rpm is not None and minute.requests >= limits.rpm:
            return False
        if limits.tpm is not None and minute.tokens_in >= limits.tpm:
            return False
        if limits.rpd is not None and daily.requests >= limits.rpd:
            return False
        await self._store.reserve(project_id, model_id, minute_ts=minute_ts, day=day)
        return True

    async def reconcile(
        self, project_id: UUID, model_id: str, *, input_tokens: int, now: datetime
    ) -> None:
        """Довнести фактические input-токены после успешного запроса (оба окна)."""
        await self._store.add_tokens(
            project_id,
            model_id,
            minute_ts=current_minute(now),
            day=pacific_day(now),
            tokens_in=input_tokens,
        )
