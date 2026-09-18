"""Unit-тесты GeminiProjectPool + QuotaTracker (failover по ADR-005, ключ по ADR-015).

Фейки in-memory: FakeProjectStore (проекты + журнал вызовов), FakeQuotaStore
(dict-счётчики). Провайдер — поддельный GeminiProvider со сценариями per-call.
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
from app.llm.errors import (
    AuthError,
    ForbiddenError,
    InvalidRequestError,
    RateLimitError,
    SafetyError,
    ServerError,
)
from app.llm.events import Done, LLMEvent, TextDelta, Usage
from app.llm.gemini.pool import GeminiProjectPool, PoolExhaustedError, ProjectInfo
from app.llm.gemini.quota import (
    QuotaLimits,
    QuotaTracker,
    UsageSnapshot,
    current_minute,
    pacific_day,
)
from app.llm.providers.gemini import GeminiProvider

NOW = datetime(2026, 9, 18, 12, 34, 56, tzinfo=UTC)
MODEL = "gemini-3.8-flash"


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


def _request() -> LLMRequest:
    return LLMRequest(
        model=MODEL, messages=[{"role": "user", "parts": [{"type": "text", "text": "hi"}]}]
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
    """Сценарный провайдер: каждый вызов stream_chat разыгрывает очередной script.

    Script — список событий; встреченный Exception поднимается в этой точке.
    """

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


# --- helpers квоты ---------------------------------------------------------


def test_current_minute_truncates_to_utc_minute() -> None:
    assert current_minute(NOW) == datetime(2026, 9, 18, 12, 34, tzinfo=UTC)
    aware = datetime(2026, 9, 18, 8, 34, 59, tzinfo=UTC)  # 01:34 PDT
    assert current_minute(aware) == datetime(2026, 9, 18, 8, 34, tzinfo=UTC)


def test_pacific_day_uses_los_angeles_date() -> None:
    assert pacific_day(NOW) == date(2026, 9, 18)  # 12:34 UTC = 05:34 PDT
    # 06:59 UTC = 23:59 PDT предыдущих суток — RPD ещё «вчера».
    assert pacific_day(datetime(2026, 9, 18, 6, 59, tzinfo=UTC)) == date(2026, 9, 17)
    # 07:00 UTC = 00:00 PDT — суточная квота сброшена.
    assert pacific_day(datetime(2026, 9, 18, 7, 0, tzinfo=UTC)) == date(2026, 9, 18)


# --- acquire: ротация и фильтры --------------------------------------------


async def test_round_robin_alternates_projects() -> None:
    p1, p2 = _project("p1"), _project("p2")
    pool, _, _ = _make_pool([p1, p2])
    got = [await pool.acquire(MODEL, now=NOW) for _ in range(4)]
    assert [c.project_id for c in got if c] == [p1.id, p2.id, p1.id, p2.id]
    assert got[0] is not None and got[0].api_key == "plain:enc:p1"


async def test_disabled_project_is_skipped() -> None:
    p1, p2 = _project("p1", enabled=False), _project("p2")
    pool, _, _ = _make_pool([p1, p2])
    cred = await pool.acquire(MODEL, now=NOW)
    assert cred is not None and cred.project_id == p2.id


async def test_cooldown_filters_only_future() -> None:
    future = _project("future", cooldown_until=NOW + timedelta(seconds=60))
    past = _project("past", cooldown_until=NOW - timedelta(seconds=1))
    pool, _, _ = _make_pool([future, past])
    cred = await pool.acquire(MODEL, now=NOW)
    assert cred is not None and cred.project_id == past.id
    cred2 = await pool.acquire(MODEL, now=NOW)
    assert cred2 is not None and cred2.project_id == past.id


async def test_acquire_empty_pool_returns_none() -> None:
    pool, _, _ = _make_pool([_project("off", enabled=False)])
    assert await pool.acquire(MODEL, now=NOW) is None


# --- квоты ------------------------------------------------------------------


async def test_quota_rpm_exhausts_project() -> None:
    """rpm=1: первый reserve успешен, второй check_and_reserve → False (None)."""
    p1 = _project("p1")
    pool, _, _ = _make_pool([p1], QuotaLimits(rpm=1))
    assert await pool.acquire(MODEL, now=NOW) is not None
    assert await pool.acquire(MODEL, now=NOW) is None
    # На исчерпанном проекте reserve не вызывается повторно.
    pool2, _, quota2 = _make_pool([p1], QuotaLimits(rpm=1))
    tracker = QuotaTracker(quota2, QuotaLimits(rpm=1))
    assert await tracker.check_and_reserve(p1.id, MODEL, now=NOW) is True
    assert await tracker.check_and_reserve(p1.id, MODEL, now=NOW) is False
    assert quota2.reserve_calls == [(p1.id, MODEL)]


async def test_quota_tpm_none_does_not_limit() -> None:
    p1 = _project("p1")
    quota_store = FakeQuotaStore()
    await quota_store.add_tokens(
        p1.id,
        MODEL,
        minute_ts=current_minute(NOW),
        day=pacific_day(NOW),
        tokens_in=10**9,
    )
    tracker = QuotaTracker(quota_store, QuotaLimits(rpm=100, tpm=None, rpd=100))
    assert await tracker.check_and_reserve(p1.id, MODEL, now=NOW) is True


async def test_quota_rpd_reached_blocks() -> None:
    p1 = _project("p1")
    quota_store = FakeQuotaStore()
    await quota_store.reserve(p1.id, MODEL, minute_ts=current_minute(NOW), day=pacific_day(NOW))
    quota_store.reserve_calls.clear()  # посев не считаем
    tracker = QuotaTracker(quota_store, QuotaLimits(rpd=1))
    assert await tracker.check_and_reserve(p1.id, MODEL, now=NOW) is False
    assert quota_store.reserve_calls == []  # блок без reserve


# --- failover (ADR-005) ------------------------------------------------------


async def test_429_rotates_to_next_project_and_reports_success() -> None:
    p1, p2 = _project("p1"), _project("p2")
    pool, store, _ = _make_pool([p1, p2])
    provider = FakeProvider()
    provider.push(RateLimitError("quota", retry_after=None))
    provider.push(TextDelta("hi"), Usage(input_tokens=7), Done("stop"))

    events = await _collect(pool, provider)

    assert events == [TextDelta("hi"), Usage(input_tokens=7), Done("stop")]
    assert len(provider.calls) == 2
    assert provider.calls[0].metadata["api_key"] == "plain:enc:p1"
    assert provider.calls[1].metadata["api_key"] == "plain:enc:p2"
    assert store.calls.set_cooldown == [(p1.id, NOW + timedelta(seconds=60))]
    assert store.calls.mark_success == [p2.id]
    # Проект в cooldown больше не выдаётся.
    cred = await pool.acquire(MODEL, now=NOW)
    assert cred is not None and cred.project_id == p2.id


async def test_429_retry_after_overrides_default_cooldown() -> None:
    p1 = _project("p1")
    pool, store, _ = _make_pool([p1])
    await pool.report_error(p1.id, MODEL, RateLimitError("q", retry_after=4.0), now=NOW)
    assert store.calls.set_cooldown == [(p1.id, NOW + timedelta(seconds=4.0))]
    assert len(store.calls.mark_error) == 1


async def test_401_disables_project_and_rotates() -> None:
    p1, p2 = _project("p1"), _project("p2")
    pool, store, _ = _make_pool([p1, p2])
    provider = FakeProvider()
    provider.push(AuthError("x" * 500, raw_code="UNAUTHENTICATED"))
    provider.push(TextDelta("ok"), Done("stop"))

    events = await _collect(pool, provider)

    assert events == [TextDelta("ok"), Done("stop")]
    pid, code, message = store.calls.mark_unhealthy[0]
    assert (pid, code) == (p1.id, "UNAUTHENTICATED")
    assert len(message) == 256  # message обрезан
    assert store.projects[0].enabled is False  # disable в сторе
    assert store.calls.set_cooldown == []
    assert provider.calls[1].metadata["api_key"] == "plain:enc:p2"


async def test_403_permission_denied_disables_project() -> None:
    """403 PERMISSION_DENIED (мёртвый Google-проект) → disable, а не cooldown-цикл."""
    p1, p2 = _project("p1"), _project("p2")
    pool, store, _ = _make_pool([p1, p2])
    provider = FakeProvider()
    provider.push(ForbiddenError("denied", raw_code="PERMISSION_DENIED"))
    provider.push(TextDelta("ok"), Done("stop"))

    events = await _collect(pool, provider)

    assert events == [TextDelta("ok"), Done("stop")]
    pid, code, _ = store.calls.mark_unhealthy[0]
    assert (pid, code) == (p1.id, "PERMISSION_DENIED")
    assert store.projects[0].enabled is False
    assert store.calls.set_cooldown == []


async def test_403_generic_gets_cooldown_not_disable() -> None:
    """Прочий 403 (не PERMISSION_DENIED) → cooldown, проект остаётся включён."""
    p1, p2 = _project("p1"), _project("p2")
    pool, store, _ = _make_pool([p1, p2])
    provider = FakeProvider()
    provider.push(ForbiddenError("quota project issue", raw_code="FORBIDDEN"))
    provider.push(TextDelta("ok"), Done("stop"))

    events = await _collect(pool, provider)

    assert events == [TextDelta("ok"), Done("stop")]
    assert store.calls.mark_unhealthy == []
    assert len(store.calls.set_cooldown) == 1
    assert store.projects[0].enabled is True


async def test_400_invalid_request_no_rotation() -> None:
    p1, p2 = _project("p1"), _project("p2")
    pool, store, _ = _make_pool([p1, p2])
    provider = FakeProvider()
    provider.push(InvalidRequestError("bad request"))
    provider.push(TextDelta("never"))

    with pytest.raises(InvalidRequestError):
        await _collect(pool, provider)

    assert len(provider.calls) == 1  # ровно одна попытка, p2 не тронут
    assert len(store.calls.mark_error) == 1
    assert store.calls.set_cooldown == [] and store.calls.mark_unhealthy == []


async def test_safety_no_rotation() -> None:
    p1, p2 = _project("p1"), _project("p2")
    pool, store, _ = _make_pool([p1, p2])
    provider = FakeProvider()
    provider.push(SafetyError("blocked"))
    provider.push(TextDelta("never"))

    with pytest.raises(SafetyError):
        await _collect(pool, provider)

    assert len(provider.calls) == 1
    assert store.calls.set_cooldown == [] and store.calls.mark_unhealthy == []


async def test_error_after_first_event_not_rotated() -> None:
    """Partial stream не перезапускаем: ошибка после TextDelta — наверх."""
    p1, p2 = _project("p1"), _project("p2")
    pool, store, _ = _make_pool([p1, p2])
    provider = FakeProvider()
    provider.push(TextDelta("partial"), ServerError("boom"))
    provider.push(TextDelta("never"))

    events: list[LLMEvent] = []
    with pytest.raises(ServerError):
        async for event in pool.stream_with_failover(provider, _request(), now_fn=lambda: NOW):
            events.append(event)

    assert events == [TextDelta("partial")]
    assert len(provider.calls) == 1
    # Ошибка всё же зарегистрирована (cooldown + mark_error), но ротации не было.
    assert store.calls.set_cooldown == [(p1.id, NOW + timedelta(seconds=30))]


async def test_pool_exhausted_raises() -> None:
    p1 = _project("p1")
    pool, _, _ = _make_pool([p1])
    provider = FakeProvider()
    provider.push(RateLimitError("q"))
    provider.push(TextDelta("never"))

    with pytest.raises(PoolExhaustedError, match=MODEL):
        await _collect(pool, provider)

    assert len(provider.calls) == 1  # один полный проход пула


async def test_success_reconciles_input_tokens() -> None:
    p1 = _project("p1")
    pool, store, quota_store = _make_pool([p1], QuotaLimits(rpm=10))
    provider = FakeProvider()
    provider.push(Usage(input_tokens=42), TextDelta("ok"), Done("stop"))

    events = await _collect(pool, provider)

    assert events == [Usage(input_tokens=42), TextDelta("ok"), Done("stop")]
    assert store.calls.mark_success == [p1.id]
    assert quota_store.add_tokens_calls == [(p1.id, MODEL, 42)]


async def test_5xx_cooldown_30s_and_next_project() -> None:
    p1, p2 = _project("p1"), _project("p2")
    pool, store, _ = _make_pool([p1, p2])
    provider = FakeProvider()
    provider.push(ServerError("internal"))
    provider.push(TextDelta("ok"), Done("stop"))

    events = await _collect(pool, provider)

    assert events == [TextDelta("ok"), Done("stop")]
    assert store.calls.set_cooldown == [(p1.id, NOW + timedelta(seconds=30))]
    assert provider.calls[1].metadata["api_key"] == "plain:enc:p2"


async def test_cancelled_error_propagates_without_report() -> None:
    p1, p2 = _project("p1"), _project("p2")
    pool, store, _ = _make_pool([p1, p2])
    provider = FakeProvider()
    provider.push(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await _collect(pool, provider)

    assert len(provider.calls) == 1
    assert store.calls.mark_error == []
    assert store.calls.mark_unhealthy == []
    assert store.calls.set_cooldown == []
    assert store.calls.mark_success == []
