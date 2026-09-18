"""Пул Gemini-проектов: round-robin выдача ключей, квоты, cooldown, failover.

Правила ротации — ADR-005: failover только внутри пула для ТОЙ ЖЕ модели
(cross-model fallback запрещён), максимум один полный проход, ротация только
пока не эмитнуто ни одного события. Ключ подставляется в запрос через
request.metadata["api_key"] (ADR-015) — провайдер остаётся stateless.

Хранилище состояния проектов — за Protocol ProjectStore (PostgreSQL пишется
отдельным агентом); пул зависит только от интерфейсов.
"""

from __future__ import annotations

import asyncio
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
from app.llm.events import LLMEvent, Usage
from app.llm.gemini.quota import QuotaTracker
from app.llm.providers.gemini import GeminiProvider

logger = logging.getLogger(__name__)

_MAX_ERROR_MESSAGE = 256


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
    """Выданный пулом ключ проекта (уже расшифрованный)."""

    project_id: UUID
    name: str
    api_key: str


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
    ) -> None:
        self._store = store
        self._quota_for_model = quota_for_model
        self._decrypt = decrypt
        self._cooldown_429 = cooldown_429
        self._cooldown_403 = cooldown_403
        self._cooldown_transient = cooldown_transient
        self._rr_counter = 0  # in-memory round-robin offset

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

        Обход кандидатов кольцом от round-robin offset; None — подходящих нет.
        """
        excluded = exclude or set()
        candidates = [
            p
            for p in await self._store.list_all()
            if p.enabled
            and p.id not in excluded
            and (p.cooldown_until is None or p.cooldown_until <= now)
        ]
        if not candidates:
            return None
        start = self._rr_counter % len(candidates)
        self._rr_counter += 1
        quota = await self._resolve_quota(model_id)
        for offset in range(len(candidates)):
            project = candidates[(start + offset) % len(candidates)]
            if await quota.check_and_reserve(project.id, model_id, now=now):
                return PooledCredential(
                    project_id=project.id,
                    name=project.name,
                    api_key=self._decrypt(project.encrypted_api_key),
                )
            logger.info(
                "gemini pool: проект %s пропущен — квота %s исчерпана", project.name, model_id
            )
        return None

    async def report_success(
        self, project_id: UUID, model_id: str, *, input_tokens: int | None, now: datetime
    ) -> None:
        """Успех: mark_success; при известном usage — reconcile input-токенов."""
        await self._store.mark_success(project_id)
        if input_tokens is not None:
            quota = await self._resolve_quota(model_id)
            await quota.reconcile(project_id, model_id, input_tokens=input_tokens, now=now)

    async def report_error(
        self, project_id: UUID, model_id: str, error: ProviderError, *, now: datetime
    ) -> None:
        """Классификация ошибки (ADR-005): disable / cooldown / только last_error.

        model_id сейчас не влияет на обработку (cooldown ставим на проект);
        параметр оставлен для симметрии API и будущих per-model cooldown.
        """
        code = error.raw_code
        message = str(error)[:_MAX_ERROR_MESSAGE]
        match error.category:
            case ErrorCategory.AUTH:
                await self._store.mark_unhealthy(project_id, error_code=code, error_message=message)
            case ErrorCategory.FORBIDDEN:
                await self._store.set_cooldown(
                    project_id, now + timedelta(seconds=self._cooldown_403)
                )
                await self._store.mark_error(project_id, error_code=code, error_message=message)
            case ErrorCategory.RATE_LIMIT:
                delay = error.retry_after if error.retry_after is not None else self._cooldown_429
                await self._store.set_cooldown(project_id, now + timedelta(seconds=delay))
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
        перезапускаем). 400/safety — сразу наверх. CancelledError — проброс без
        report_error. Пул исчерпан — PoolExhaustedError.
        """
        tried: set[UUID] = set()
        while True:
            cred = await self.acquire(request.model, exclude=tried, now=now_fn())
            if cred is None:
                raise PoolExhaustedError(request.model)
            req2 = dataclasses.replace(
                request, metadata={**request.metadata, "api_key": cred.api_key}
            )
            events_started = False
            last_usage: Usage | None = None
            try:
                async for event in provider.stream_chat(req2):
                    events_started = True
                    if isinstance(event, Usage):
                        last_usage = event
                    yield event
                await self.report_success(
                    cred.project_id,
                    request.model,
                    input_tokens=last_usage.input_tokens if last_usage else None,
                    now=now_fn(),
                )
                return
            except asyncio.CancelledError:
                raise
            except ProviderError as e:
                await self.report_error(cred.project_id, request.model, e, now=now_fn())
                if e.category in (ErrorCategory.INVALID_REQUEST, ErrorCategory.SAFETY):
                    raise
                if events_started:
                    raise
                logger.warning(
                    "gemini pool: проект %s дал %s — ротация на следующий",
                    cred.name,
                    e.category,
                )
                tried.add(cred.project_id)
