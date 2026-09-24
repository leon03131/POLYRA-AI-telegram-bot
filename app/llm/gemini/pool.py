"""Пул Gemini-проектов: round-robin выдача ключей, квоты, cooldown, failover.

Правила ротации — ADR-005: failover только внутри пула для ТОЙ ЖЕ модели
(cross-model fallback запрещён), максимум один полный проход, ротация только
пока не эмитнуто ни одного события. Ключ подставляется в запрос через
request.metadata["api_key"] (ADR-015) — провайдер остаётся stateless.

A10: cooldown 429 — per (project, model) in-memory (DB-колонка cooldown_until
остаётся project-level: схему меняет владелец G); server/network/timeout —
один bounded-retry того же проекта (задержка retry_delay) перед ротацией,
только пока не эмитнуто ни одного события.

Хранилище состояния проектов — за Protocol ProjectStore (PostgreSQL пишется
отдельным агентом); пул зависит только от интерфейсов.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.llm.base import LLMRequest
from app.llm.errors import ErrorCategory, ProviderError
from app.llm.events import Done, LLMEvent, Usage
from app.llm.gemini.quota import QuotaReservation, QuotaTracker, current_minute, pacific_day
from app.llm.providers.gemini import GeminiProvider

logger = logging.getLogger(__name__)

_MAX_ERROR_MESSAGE = 256

# A10: категории с одним повтором того же проекта перед ротацией (pre-event).
_RETRY_SAME_PROJECT = frozenset(
    {ErrorCategory.SERVER, ErrorCategory.NETWORK, ErrorCategory.TIMEOUT}
)


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    """Снимок проекта из БД; порядок в list_all задаёт rotation_order."""

    id: UUID
    name: str
    enabled: bool
    cooldown_until: datetime | None
    encrypted_api_key: str


class ProjectStore(Protocol):
    """Хранилище состояния проектов (реализация — PostgreSQL, вне этого модуля)."""

    async def list_all(self) -> list[ProjectInfo]:
        """Все проекты в порядке rotation_order."""
        ...

    async def set_cooldown(self, project_id: UUID, until: datetime | None) -> None: ...

    async def mark_unhealthy(
        self, project_id: UUID, *, error_code: str | None, error_message: str
    ) -> None:
        """401: проект мёртв — disable (health=unhealthy) + last_error_*."""
        ...

    async def mark_error(
        self, project_id: UUID, *, error_code: str | None, error_message: str
    ) -> None:
        """Прочие ошибки: только last_error_* (без смены enabled)."""
        ...

    async def mark_success(self, project_id: UUID) -> None: ...


@dataclass(frozen=True, slots=True)
class PooledCredential:
    """Выданный пулом ключ проекта (уже расшифрованный).

    quota_reservation — окна квоты момента выдачи (A09); report_success
    reconcile'ит токены строго в них, а не в окна «текущего» момента.
    """

    project_id: UUID
    name: str
    api_key: str
    quota_reservation: QuotaReservation | None = None


class PoolExhaustedError(Exception):
    """Все Gemini projects для этой модели сейчас недоступны."""

    def __init__(self, model_id: str) -> None:
        super().__init__(f"Все Gemini projects для модели {model_id} сейчас недоступны")
        self.model_id = model_id


class GeminiProjectPool:
    """Ротация проектов Gemini с квотами и cooldown по ADR-005."""

    def __init__(
        self,
        *,
        store: ProjectStore,
        quota_for_model: Callable[[str], QuotaTracker | Awaitable[QuotaTracker]],
        decrypt: Callable[[str], str],
        cooldown_429: float = 60.0,
        cooldown_403: float = 300.0,
        cooldown_transient: float = 30.0,
        retry_delay: float = 0.5,
    ) -> None:
        self._store = store
        self._quota_for_model = quota_for_model
        self._decrypt = decrypt
        self._cooldown_429 = cooldown_429
        self._cooldown_403 = cooldown_403
        self._cooldown_transient = cooldown_transient
        self._retry_delay = retry_delay
        self._rr_counter = 0  # in-memory round-robin offset
        # A10: 429-cooldown per (project, model) — process-local, БД-схему не меняем.
        self._model_cooldowns: dict[tuple[UUID, str], datetime] = {}

    async def _resolve_quota(self, model_id: str) -> QuotaTracker:
        """Фабрика quota может быть sync или async (DB-вариант) — поддерживаем оба."""
        tracker = self._quota_for_model(model_id)
        if inspect.isawaitable(tracker):
            return await tracker
        return tracker

    async def acquire(
        self, model_id: str, *, exclude: set[UUID] | None = None, now: datetime
    ) -> PooledCredential | None:
        """Выдать ключ: enabled, без активного cooldown, вне exclude, квота не исчерпана.

        Cooldown-фильтры: project-level из БД (cooldown_until) + in-memory
        per (project, model) для 429 (A10). Обход кандидатов кольцом от
        round-robin offset; None — подходящих нет.
        """
        excluded = exclude or set()
        candidates = [
            p
            for p in await self._store.list_all()
            if p.enabled
            and p.id not in excluded
            and (p.cooldown_until is None or p.cooldown_until <= now)
            and not self._model_cooldown_active(p.id, model_id, now)
        ]
        if not candidates:
            return None
        start = self._rr_counter % len(candidates)
        self._rr_counter += 1
        quota = await self._resolve_quota(model_id)
        for offset in range(len(candidates)):
            project = candidates[(start + offset) % len(candidates)]
            reservation = await quota.check_and_reserve(project.id, model_id, now=now)
            if reservation is not None:
                return PooledCredential(
                    project_id=project.id,
                    name=project.name,
                    api_key=self._decrypt(project.encrypted_api_key),
                    quota_reservation=reservation,
                )
            logger.info(
                "gemini pool: проект %s пропущен — квота %s исчерпана", project.name, model_id
            )
        return None

    def _model_cooldown_active(self, project_id: UUID, model_id: str, now: datetime) -> bool:
        """Активен ли in-memory 429-cooldown для (project, model); протухший удаляется."""
        until = self._model_cooldowns.get((project_id, model_id))
        if until is None:
            return False
        if until <= now:
            del self._model_cooldowns[(project_id, model_id)]
            return False
        return True

    async def report_success(
        self,
        project_id: UUID,
        model_id: str,
        *,
        input_tokens: int | None,
        now: datetime,
        reservation: QuotaReservation | None = None,
    ) -> None:
        """Успех: mark_success; при известном usage — reconcile input-токенов.

        reconcile идёт в окна РЕЗЕРВАЦИИ (A09); reservation=None (вызов вне
        acquire) — fallback на окна момента `now`.
        """
        await self._store.mark_success(project_id)
        if input_tokens is not None:
            quota = await self._resolve_quota(model_id)
            if reservation is None:
                reservation = QuotaReservation(
                    project_id=project_id,
                    model_id=model_id,
                    minute_ts=current_minute(now),
                    day=pacific_day(now),
                )
            await quota.reconcile(reservation, input_tokens=input_tokens)

    async def report_error(
        self, project_id: UUID, model_id: str, error: ProviderError, *, now: datetime
    ) -> None:
        """Классификация ошибки (ADR-005): disable / cooldown / только last_error.

        429 (RATE_LIMIT) — in-memory cooldown строго per (project, model):
        другие модели того же проекта продолжают обслуживаться (A10); БД
        cooldown_until (project-level, схема — владелец G) не трогается.
        """
        code = error.raw_code
        message = str(error)[:_MAX_ERROR_MESSAGE]
        match error.category:
            case ErrorCategory.AUTH:
                await self._store.mark_unhealthy(project_id, error_code=code, error_message=message)
            case ErrorCategory.FORBIDDEN:
                # PERMISSION_DENIED «project denied access» = мёртвый проект Google —
                # выключаем сразу, иначе будет гореть cooldown-циклами в каждой ротации.
                if code == "PERMISSION_DENIED":
                    await self._store.mark_unhealthy(
                        project_id, error_code=code, error_message=message
                    )
                    return
                await self._store.set_cooldown(
                    project_id, now + timedelta(seconds=self._cooldown_403)
                )
                await self._store.mark_error(project_id, error_code=code, error_message=message)
            case ErrorCategory.RATE_LIMIT:
                delay = error.retry_after if error.retry_after is not None else self._cooldown_429
                self._model_cooldowns[(project_id, model_id)] = now + timedelta(seconds=delay)
                await self._store.mark_error(project_id, error_code=code, error_message=message)
            case ErrorCategory.SERVER | ErrorCategory.NETWORK | ErrorCategory.TIMEOUT:
                await self._store.set_cooldown(
                    project_id, now + timedelta(seconds=self._cooldown_transient)
                )
                await self._store.mark_error(project_id, error_code=code, error_message=message)
            case _:
                # INVALID_REQUEST / SAFETY / UNKNOWN — проект не виноват, без cooldown.
                await self._store.mark_error(project_id, error_code=code, error_message=message)

    async def stream_with_failover(
        self,
        provider: GeminiProvider,
        request: LLMRequest,
        *,
        now_fn: Callable[[], datetime],
    ) -> AsyncIterator[LLMEvent]:
        """Стрим с ротацией проектов: максимум один полный проход пула.

        Перезапуск только если не эмитнуто ни одного события (partial stream не
        перезапускаем). Server/network/timeout — перед ротацией ОДИН повтор того
        же проекта с задержкой retry_delay (A10 bounded retry, только до первого
        события). 400/safety — сразу наверх. CancelledError — проброс без
        report_error. Пул исчерпан — PoolExhaustedError.
        """
        tried: set[UUID] = set()
        while True:
            cred = await self.acquire(request.model, exclude=tried, now=now_fn())
            if cred is None:
                raise PoolExhaustedError(request.model)
            self._record_attempt(request, cred)
            req2 = dataclasses.replace(
                request, metadata={**request.metadata, "api_key": cred.api_key}
            )
            events_started = False
            retried = False
            while True:
                try:
                    async for event in self._stream_attempt(provider, req2, request, cred, now_fn):
                        events_started = True
                        yield event
                    return  # стрим дошёл до конца — успех
                except asyncio.CancelledError:
                    raise
                except ProviderError as e:
                    if not retried and not events_started and e.category in _RETRY_SAME_PROJECT:
                        # A10: один повтор того же проекта до ротации; partial
                        # stream (events_started) ретраить нельзя.
                        retried = True
                        logger.info(
                            "gemini pool: проект %s дал %s — повтор той же попытки через %.1fs",
                            cred.name,
                            e.category,
                            self._retry_delay,
                        )
                        await asyncio.sleep(self._retry_delay)
                        continue
                    await self.report_error(cred.project_id, request.model, e, now=now_fn())
                    if e.category in (ErrorCategory.INVALID_REQUEST, ErrorCategory.SAFETY):
                        raise
                    if events_started:
                        raise  # partial stream не перезапускаем (N03)
                    logger.warning(
                        "gemini pool: проект %s дал %s — ротация на следующий",
                        cred.name,
                        e.category,
                    )
                    tried.add(cred.project_id)
                    break

    @staticmethod
    def _record_attempt(request: LLMRequest, cred: PooledCredential) -> None:
        """Записать попытку в metadata["attempts"]/["attempt_ids"] (если переданы)."""
        attempts = request.metadata.get("attempts")
        if isinstance(attempts, list):
            attempts.append(cred.name)
        attempt_ids = request.metadata.get("attempt_ids")
        if isinstance(attempt_ids, list):
            attempt_ids.append(str(cred.project_id))

    async def _stream_attempt(
        self,
        provider: GeminiProvider,
        req2: LLMRequest,
        request: LLMRequest,
        cred: PooledCredential,
        now_fn: Callable[[], datetime],
    ) -> AsyncIterator[LLMEvent]:
        """Одна попытка стрима; успех (в т.ч. ранний уход после Done) → report_success.

        Ошибки ProviderError пробрасываются наверх для обработки ротации.
        """
        terminal_seen = False
        last_usage: Usage | None = None
        try:
            async for event in provider.stream_chat(req2):
                if isinstance(event, Usage):
                    last_usage = event
                if isinstance(event, Done):
                    terminal_seen = True
                yield event
        finally:
            # Нормальный конец ИЛИ потребитель ушёл после Done (break) — успех.
            # Ровно один report_success/reconcile на попытку (идемпотентность
            # reconcile — контрактом вызывающей стороны, см. QuotaTracker).
            if terminal_seen:
                with contextlib.suppress(Exception):
                    await self.report_success(
                        cred.project_id,
                        request.model,
                        input_tokens=last_usage.input_tokens if last_usage else None,
                        now=now_fn(),
                        reservation=cred.quota_reservation,
                    )
