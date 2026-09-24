"""FIX_V2 §1/§5 — GeminiProjectPool: usage-trailer при раннем break потребителя,
scope cooldown, bounded retry, attempts-ledger, PoolExhausted. Offline: фейки
in-memory (стиль tests/unit/test_gemini_pool.py), сети и БД нет.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.llm.base import LLMRequest
from app.llm.errors import RateLimitError, ServerError
from app.llm.events import Done, LLMEvent, TextDelta, Usage
from app.llm.gemini.pool import GeminiProjectPool, PoolExhaustedError, ProjectInfo
from app.llm.gemini.quota import QuotaLimits, QuotaTracker, UsageSnapshot
from app.llm.providers.gemini import GeminiProvider

NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=UTC)
MODEL = "gemini-3.8-flash"
MODEL_B = "gemini-3.7-flash"


def _project(
    name: str, *, enabled: bool = True, cooldown_until: datetime | None = None
) -> ProjectInfo:
    return ProjectInfo(
        id=uuid4(),
        name=name,
        enabled=enabled,
        cooldown_until=cooldown_until,
        encrypted_api_key=f"enc:{name}",
    )


def _decrypt(value: str) -> str:
    return f"plain:{value}"


def _request(**kw: object) -> LLMRequest:
    return LLMRequest(
        model=MODEL, messages=[{"role": "user", "parts": [{"type": "text", "text": "hi"}]}], **kw
    )


@dataclass
class _StoreCalls:
    set_cooldown: list[tuple[UUID, datetime | None]] = field(default_factory=list)
    mark_unhealthy: list[tuple[UUID, str | None, str]] = field(default_factory=list)
    mark_error: list[tuple[UUID, str | None, str]] = field(default_factory=list)
    mark_success: list[UUID] = field(default_factory=list)


class FakeProjectStore:
    """In-memory ProjectStore: список проектов + журнал вызовов."""

    def __init__(self, projects: list[ProjectInfo]) -> None:
        self.projects = list(projects)
        self.calls = _StoreCalls()

    def _replace(self, project_id: UUID, **changes: object) -> None:
        for i, p in enumerate(self.projects):
            if p.id == project_id:
                self.projects[i] = dataclasses.replace(p, **changes)

    async def list_all(self) -> list[ProjectInfo]:
        return list(self.projects)

    async def set_cooldown(self, project_id: UUID, until: datetime | None) -> None:
        self.calls.set_cooldown.append((project_id, until))
        self._replace(project_id, cooldown_until=until)

    async def mark_unhealthy(
        self, project_id: UUID, *, error_code: str | None, error_message: str
    ) -> None:
        self.calls.mark_unhealthy.append((project_id, error_code, error_message))
        self._replace(project_id, enabled=False)

    async def mark_error(
        self, project_id: UUID, *, error_code: str | None, error_message: str
    ) -> None:
        self.calls.mark_error.append((project_id, error_code, error_message))

    async def mark_success(self, project_id: UUID) -> None:
        self.calls.mark_success.append(project_id)


class FakeQuotaStore:
    """In-memory QuotaStore: dict-счётчики окон + журнал reserve/add_tokens."""

    def __init__(self) -> None:
        self.minute: dict[tuple[UUID, str, datetime], dict[str, int]] = {}
        self.daily: dict[tuple[UUID, str, date], dict[str, int]] = {}
        self.reserve_calls: list[tuple[UUID, str]] = []
        self.add_tokens_calls: list[tuple[UUID, str, int]] = []

    async def get_minute_usage(
        self, project_id: UUID, model_id: str, minute_ts: datetime
    ) -> UsageSnapshot:
        row = self.minute.get((project_id, model_id, minute_ts), {})
        return UsageSnapshot(requests=row.get("requests", 0), tokens_in=row.get("tokens_in", 0))

    async def get_daily_usage(self, project_id: UUID, model_id: str, day: date) -> UsageSnapshot:
        row = self.daily.get((project_id, model_id, day), {})
        return UsageSnapshot(requests=row.get("requests", 0), tokens_in=row.get("tokens_in", 0))

    async def reserve(
        self, project_id: UUID, model_id: str, *, minute_ts: datetime, day: date
    ) -> None:
        self.reserve_calls.append((project_id, model_id))
        self.minute.setdefault((project_id, model_id, minute_ts), {"requests": 0, "tokens_in": 0})[
            "requests"
        ] += 1
        self.daily.setdefault((project_id, model_id, day), {"requests": 0, "tokens_in": 0})[
            "requests"
        ] += 1

    async def add_tokens(
        self,
        project_id: UUID,
        model_id: str,
        *,
        minute_ts: datetime,
        day: date,
        tokens_in: int,
    ) -> None:
        self.add_tokens_calls.append((project_id, model_id, tokens_in))
        self.minute.setdefault((project_id, model_id, minute_ts), {"requests": 0, "tokens_in": 0})[
            "tokens_in"
        ] += tokens_in
        self.daily.setdefault((project_id, model_id, day), {"requests": 0, "tokens_in": 0})[
            "tokens_in"
        ] += tokens_in


class FakeProvider(GeminiProvider):
    """Сценарный провайдер: каждый вызов stream_chat разыгрывает очередной script."""

    def __init__(self) -> None:
        self.scripts: list[list[LLMEvent | BaseException]] = []
        self.calls: list[LLMRequest] = []

    def push(self, *items: LLMEvent | BaseException) -> None:
        self.scripts.append(list(items))

    async def stream_chat(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        self.calls.append(request)
        script = self.scripts.pop(0) if self.scripts else []
        for item in script:
            if isinstance(item, BaseException):
                raise item
            yield item


def _make_pool(
    projects: list[ProjectInfo], limits: QuotaLimits | None = None, **kw: object
) -> tuple[GeminiProjectPool, FakeProjectStore, FakeQuotaStore]:
    store = FakeProjectStore(projects)
    quota_store = FakeQuotaStore()
    effective = limits or QuotaLimits()
    pool = GeminiProjectPool(
        store=store,
        quota_for_model=lambda _model: QuotaTracker(quota_store, effective),
        decrypt=_decrypt,
        **kw,
    )
    return pool, store, quota_store


async def _collect(pool: GeminiProjectPool, provider: FakeProvider) -> list[LLMEvent]:
    return [
        event async for event in pool.stream_with_failover(provider, _request(), now_fn=lambda: NOW)
    ]


async def _drain_loop(predicate: object, *, times: int = 20) -> None:
    """Дать event loop обработать asyncgen-finalizer вложенного генератора.

    Потребитель закрывает ВНЕШНИЙ генератор пула (aclose, как _consume в
    finally); finally ВНУТРЕННЕГО _stream_attempt (там report_success/reconcile)
    выполняет finalizer event loop'а (sys.set_asyncgen_hooks ставит asyncio) —
    ему нужны итерации цикла. Тот же механизм работает в production под
    asyncio.run. Опробовано на CPython 3.14.7 + pytest-asyncio 1.4.
    """
    for _ in range(times):
        if predicate():  # type: ignore[operator]
            return
        await asyncio.sleep(0)


# --- §1: usage-trailer не теряется при break потребителя после Done ------------


async def test_usage_trailer_survives_consumer_break_after_done() -> None:
    """Потребитель читает до Done и break (как GenerationService._consume) →
    report_success вызван ровно один раз, input-токены из usage-trailer
    доехали до reconcile (не потеряны)."""
    p1 = _project("p1")
    pool, store, quota_store = _make_pool([p1], QuotaLimits(rpm=10))
    provider = FakeProvider()
    provider.push(TextDelta("hi"), Usage(input_tokens=7, output_tokens=3), Done("stop"))

    seen: list[LLMEvent] = []
    gen = pool.stream_with_failover(provider, _request(), now_fn=lambda: NOW)
    try:
        async for event in gen:
            seen.append(event)
            if isinstance(event, Done):
                break  # потребитель ушёл сразу после Done
    finally:
        await gen.aclose()  # дисциплина _consume: стрим закрывается всегда

    assert seen == [TextDelta("hi"), Usage(input_tokens=7, output_tokens=3), Done("stop")]
    await _drain_loop(lambda: bool(store.calls.mark_success))
    assert store.calls.mark_success == [p1.id]  # ровно один раз
    assert quota_store.add_tokens_calls == [(p1.id, MODEL, 7)]  # usage-trailer не потерян


# --- §5: cooldown scope, bounded retry, attempts, exhaustion -------------------


async def test_429_cooldown_is_scoped_per_project_and_model() -> None:
    """429 на модели A → in-memory cooldown (project, A); тот же проект доступен
    модели B; после истечения cooldown проект снова доступен модели A (§5/A10)."""
    p1 = _project("p1")
    pool, store, _ = _make_pool([p1])

    await pool.report_error(p1.id, MODEL, RateLimitError("quota"), now=NOW)

    # В рамках той же модели cooldown действует.
    assert await pool.acquire(MODEL, now=NOW) is None
    # Другая модель того же проекта НЕ затронута (§5: project+model scope).
    cred = await pool.acquire(MODEL_B, now=NOW)
    assert cred is not None and cred.project_id == p1.id
    # Cooldown конечный (default 60s): после истечения модель A снова обслуживается.
    later = NOW + timedelta(seconds=61)
    cred_after = await pool.acquire(MODEL, now=later)
    assert cred_after is not None and cred_after.project_id == p1.id
    # 429 не гасит проект целиком: project-level БД-cooldown не трогается,
    # запись об ошибке (last_error_*) — есть.
    assert store.calls.set_cooldown == []
    assert len(store.calls.mark_error) == 1


async def test_server_error_bounded_retry_same_project_then_rotation() -> None:
    """ServerError → тот же проект повторяется один раз → затем ротация на next."""
    p1, p2 = _project("p1"), _project("p2")
    pool, store, _ = _make_pool([p1, p2], retry_delay=0.0)  # без реального sleep
    provider = FakeProvider()
    provider.push(ServerError("boom-1"))  # p1: исходная попытка
    provider.push(ServerError("boom-2"))  # p1: bounded retry (контракт §5)
    provider.push(TextDelta("ok"), Usage(input_tokens=3), Done("stop"))  # p2 после ротации

    events = await _collect(pool, provider)

    assert [c.metadata["api_key"] for c in provider.calls] == [
        "plain:enc:p1",
        "plain:enc:p1",  # повтор того же проекта до ротации
        "plain:enc:p2",
    ]
    assert events == [TextDelta("ok"), Usage(input_tokens=3), Done("stop")]
    assert store.calls.mark_success == [p2.id]


async def test_attempts_recorded_in_request_metadata() -> None:
    """Каждая попытка пула пишется в metadata['attempts']/['attempt_ids'] (A07)."""
    p1, p2 = _project("p1"), _project("p2")
    pool, store, _ = _make_pool([p1, p2])
    provider = FakeProvider()
    provider.push(RateLimitError("quota"))
    provider.push(TextDelta("ok"), Usage(input_tokens=5), Done("stop"))

    attempts: list[str] = []
    attempt_ids: list[str] = []
    request = _request(metadata={"attempts": attempts, "attempt_ids": attempt_ids})
    events = [
        event async for event in pool.stream_with_failover(provider, request, now_fn=lambda: NOW)
    ]

    assert events == [TextDelta("ok"), Usage(input_tokens=5), Done("stop")]
    assert attempts == ["p1", "p2"]
    assert attempt_ids == [str(p1.id), str(p2.id)]
    assert store.calls.mark_success == [p2.id]


async def test_pool_exhausted_when_all_projects_fail() -> None:
    """Все проекты дали retryable-ошибку → ровно один полный проход → PoolExhausted."""
    p1, p2 = _project("p1"), _project("p2")
    pool, store, _ = _make_pool([p1, p2])
    provider = FakeProvider()
    provider.push(RateLimitError("q1"))
    provider.push(RateLimitError("q2"))

    with pytest.raises(PoolExhaustedError, match=MODEL):
        await _collect(pool, provider)

    assert len(provider.calls) == 2  # один полный проход пула, без второго круга
    assert store.calls.mark_success == []
    assert len(store.calls.mark_error) == 2  # обе ошибки зарегистрированы
    # 429-cooldown in-memory per (project, model): для этой модели пул пуст...
    assert await pool.acquire(MODEL, now=NOW) is None
    # ...но для другой модели проекты доступны (§5).
    cred = await pool.acquire(MODEL_B, now=NOW)
    assert cred is not None
