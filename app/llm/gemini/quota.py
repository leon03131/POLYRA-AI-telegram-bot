"""Учёт квот Gemini-проектов: RPM / TPM(input) / RPD.

Окна измерений (docs/vendor/GEMINI.md): минутные счётчики по UTC-минуте,
суточные — по дате в America/Los_Angeles (ресет RPD в полночь Pacific).
Хранилище спрятано за Protocol QuotaStore (реализация — PostgreSQL, вне
этого модуля), поэтому трекер полностью тестируем in-memory.

A09: если store реализует `check_and_reserve_atomic`, трекер использует её —
check+reserve выполняются одной БД-транзакцией (два параллельных запроса не
проходят одновременно при исчерпанном лимите). Иначе — legacy read+reserve.
Резервация возвращается как QuotaReservation с окнами МОМЕНТА резервации;
reconcile пишет фактические токены строго в эти окна (переход через границу
минуты/суток между reserve и reconcile не уводит токены в чужое окно).
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


@dataclass(frozen=True, slots=True)
class QuotaReservation:
    """Подтверждённая резервация запроса: окна, в которые она записана (A09).

    reconcile обязан использовать именно эти окна (минута/день МОМЕНТА
    резервации), иначе при пересечении границы окна токены попадут не туда.
    """

    project_id: UUID
    model_id: str
    minute_ts: datetime
    day: date


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

    async def check_and_reserve_atomic(
        self,
        project_id: UUID,
        model_id: str,
        *,
        minute_ts: datetime,
        day: date,
        limits: QuotaLimits,
    ) -> bool:
        """ОПЦИОНАЛЬНО (A09): атомарный check+reserve одной транзакцией.

        True — запрос допущен и requests_count инкрементирован в обоих окнах;
        False — лимит достигнут, счётчики не изменены. Лимит None не
        ограничивает. TPM сравнивается с уже учтёнными tokens_in: токены
        текущего запроса на момент reserve неизвестны и довносятся reconcile
        post-factum (local accounting, допуск сверх TPM после факта не
        блокируется). QuotaTracker вызывает метод через getattr: store без
        него обслуживается legacy-путём get_* + reserve (не атомарно).
        """
        ...


def _unlimited(limits: QuotaLimits) -> bool:
    return limits.rpm is None and limits.tpm is None and limits.rpd is None


class QuotaTracker:
    """Проверка лимитов с резервом запроса и reconcile фактических токенов."""

    def __init__(self, store: QuotaStore, limits: QuotaLimits) -> None:
        self._store = store
        self._limits = limits

    async def check_and_reserve(
        self, project_id: UUID, model_id: str, *, now: datetime
    ) -> QuotaReservation | None:
        """QuotaReservation — лимиты позволяют и запрос зарезервирован; None — отказ.

        Atomic-store (A09): check+reserve одной транзакцией. Legacy-store:
        читаем минутное и суточное окна (rpm по minute.requests, tpm по
        minute.tokens_in, rpd по daily.requests); достигнут любой из заданных
        → None, счётчики не трогаем. Все лимиты None → reserve без чтения.
        """
        limits = self._limits
        minute_ts = current_minute(now)
        day = pacific_day(now)

        atomic = getattr(self._store, "check_and_reserve_atomic", None)
        if atomic is not None:
            allowed = await atomic(
                project_id, model_id, minute_ts=minute_ts, day=day, limits=limits
            )
            if not allowed:
                return None
        else:
            if not _unlimited(limits):
                minute = await self._store.get_minute_usage(project_id, model_id, minute_ts)
                daily = await self._store.get_daily_usage(project_id, model_id, day)
                if limits.rpm is not None and minute.requests >= limits.rpm:
                    return None
                if limits.tpm is not None and minute.tokens_in >= limits.tpm:
                    return None
                if limits.rpd is not None and daily.requests >= limits.rpd:
                    return None
            await self._store.reserve(project_id, model_id, minute_ts=minute_ts, day=day)
        return QuotaReservation(
            project_id=project_id, model_id=model_id, minute_ts=minute_ts, day=day
        )

    async def reconcile(self, reservation: QuotaReservation, *, input_tokens: int) -> None:
        """Довнести фактические input-токены в окна РЕЗЕРВАЦИИ (оба окна).

        Идемпотентность — на вызывающей стороне: пул вызывает reconcile ровно
        один раз на успешную попытку (stream_with_failover, finally после
        Done). Store повторный вызов не дедуплицирует — дубль задвоит
        tokens_in, поэтому повторный reconcile запрещён контрактом.
        """
        await self._store.add_tokens(
            reservation.project_id,
            reservation.model_id,
            minute_ts=reservation.minute_ts,
            day=reservation.day,
            tokens_in=input_tokens,
        )
