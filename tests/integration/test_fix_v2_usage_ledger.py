"""FIX_V2 §2 usage ledger — _sum_usage, суммирование по раундам tool loop,
сохранение известного usage у cancelled-запусков. Offline, без БД: фейковый
stream по сценарию, фейковый streamer, реальный ToolRunner с echo-tool,
llm_tools через make_llm_tools (стиль tests/unit/test_generation.py).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import app.services.generation as generation_module
from app.config import Settings
from app.llm.base import LLMRequest
from app.llm.events import Done, LLMEvent, TextDelta, ToolCall, Usage
from app.llm.registry import default_registry
from app.llm.tools.registry import ToolDefinition, ToolRegistry
from app.llm.tools.runner import ToolRunner
from app.llm.tools.schemas import make_llm_tools
from app.services.access import evaluate_access
from app.services.generation import (
    GenerationRegistry,
    GenerationService,
    StreamOutcome,
    _PreparedGeneration,
    _sum_usage,
)

_SETTINGS = Settings(default_model="gemini-3.8-flash")

# --- _sum_usage: алгебра None-компонентов --------------------------------------


def test_sum_usage_adds_all_components() -> None:
    total = _sum_usage(
        Usage(input_tokens=100, output_tokens=20, reasoning_tokens=5, total_tokens=125),
        Usage(input_tokens=200, output_tokens=30, reasoning_tokens=7, total_tokens=237),
    )
    assert total == Usage(input_tokens=300, output_tokens=50, reasoning_tokens=12, total_tokens=362)


def test_sum_usage_partial_none_components() -> None:
    """None-компонент трактуется как 0 в сумме, если у другого слагаемого есть значение."""
    total = _sum_usage(
        Usage(input_tokens=10, output_tokens=None, reasoning_tokens=2, total_tokens=None),
        Usage(input_tokens=None, output_tokens=5, reasoning_tokens=None, total_tokens=20),
    )
    assert total == Usage(input_tokens=10, output_tokens=5, reasoning_tokens=2, total_tokens=20)


def test_sum_usage_none_plus_value() -> None:
    """Пустое аккумулирующее Usage() + частичное событие → только известные поля."""
    total = _sum_usage(Usage(), Usage(input_tokens=5))
    assert total == Usage(
        input_tokens=5, output_tokens=None, reasoning_tokens=None, total_tokens=None
    )


def test_sum_usage_none_plus_none() -> None:
    """Нет данных у обоих слагаемых → None (unknown ≠ 0, контракт §2)."""
    total = _sum_usage(Usage(), Usage())
    assert total == Usage(
        input_tokens=None, output_tokens=None, reasoning_tokens=None, total_tokens=None
    )


# --- _stream_loop: суммирование по раундам, cancelled --------------------------


class FakeStreamer:
    """Записывает append/finalize/fail без Telegram."""

    def __init__(self) -> None:
        self.appended: list[str] = []
        self.finalize_calls = 0
        self.fail_calls: list[str] = []

    async def append(self, delta: str) -> None:
        self.appended.append(delta)

    async def flush(self, *, force: bool = False) -> None:
        pass

    async def finalize(self) -> None:
        self.finalize_calls += 1

    async def fail(self, user_message: str) -> None:
        self.fail_calls.append(user_message)


async def _stream_of(events: list[LLMEvent]) -> AsyncIterator[LLMEvent]:
    for event in events:
        yield event


async def _empty_stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
    return
    yield  # pragma: no cover - async-генератор без событий


def _permissions_stub() -> Any:
    return evaluate_access(
        is_owner=True,
        user_status="active",
        grant=None,
        allowed_models=None,
        now=datetime.now(UTC),
    )


def _prepared_stub() -> _PreparedGeneration:
    return _PreparedGeneration(
        chat_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        draft_id=1,
        model_id="gemini-3.8-flash",
        model_display="Gemini 3.8 Flash",
        provider="gemini",
        thinking=None,
        system_prompt="sys",
        messages=[{"role": "user", "parts": [{"type": "text", "text": "hi"}]}],
        web_enabled=True,
    )


def _echo_engine() -> tuple[ToolRegistry, ToolRunner, list]:
    registry = ToolRegistry()

    async def echo_handler(args: dict, context: object) -> str:
        return "echo: " + str(args["text"])

    registry.register(
        ToolDefinition(
            name="echo",
            description="echo",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=echo_handler,
        )
    )
    runner = ToolRunner(registry, session_factory=None)
    llm_tools = make_llm_tools(registry.list_enabled(_permissions_stub()))
    return registry, runner, llm_tools


def _service(llm_stream: Any = None) -> GenerationService:
    return GenerationService(
        session_factory=None,
        registry=default_registry(),
        llm_stream=llm_stream or _empty_stream,
        settings=_SETTINGS,
        generation_registry=GenerationRegistry(),
    )


async def test_stream_loop_sums_usage_across_tool_rounds() -> None:
    """Два раунда (tool_call c usage 100/20, затем text c usage 200/30) →
    итоговый usage 300/50; reasoning тоже суммируется (5+7=12)."""
    calls: list[LLMRequest] = []

    def fake_stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
        calls.append(request)
        if len(calls) == 1:
            return _stream_of(
                [
                    ToolCall(id="c1", name="echo", arguments_json='{"text": "hi"}'),
                    Usage(input_tokens=100, output_tokens=20, reasoning_tokens=5, total_tokens=125),
                    Done("tool_calls"),
                ]
            )
        return _stream_of(
            [
                TextDelta("готово"),
                Usage(input_tokens=200, output_tokens=30, reasoning_tokens=7, total_tokens=237),
                Done("stop"),
            ]
        )

    _, runner, llm_tools = _echo_engine()
    service = _service(fake_stream)

    result = await service._stream_loop(
        _prepared_stub(),
        FakeStreamer(),
        llm_tools=llm_tools,
        tool_runner=runner,
        user=SimpleNamespace(id=uuid.uuid4()),
        permissions=_permissions_stub(),
        cancellation=asyncio.Event(),
    )

    assert result is not None
    outcome, tool_records, _attempts, _attempt_ids = result
    assert outcome.text == "готово"
    assert outcome.cancelled is False
    assert outcome.usage == Usage(
        input_tokens=300, output_tokens=50, reasoning_tokens=12, total_tokens=362
    )
    assert len(tool_records) == 1
    assert len(calls) == 2  # ровно два LLM-вызова — сумма именно per-call usage


async def test_stream_loop_cancelled_run_keeps_known_usage() -> None:
    """Отмена после первого раунда (tool исполнился, cancellation выставлен) →
    outcome.cancelled=True и известный usage раунда сохранён (не None, не 0)."""
    cancellation = asyncio.Event()
    calls: list[LLMRequest] = []

    def fake_stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
        calls.append(request)
        if len(calls) == 1:
            return _stream_of(
                [
                    ToolCall(id="c1", name="echo", arguments_json='{"text": "hi"}'),
                    Usage(input_tokens=100, output_tokens=20, reasoning_tokens=5, total_tokens=125),
                    Done("tool_calls"),
                ]
            )
        raise AssertionError("второй раунд не должен начаться после отмены")

    registry = ToolRegistry()

    async def cancelling_echo(args: dict, context: object) -> str:
        cancellation.set()  # stop пришёл, пока исполнялся tool первого раунда
        return "echo: " + str(args["text"])

    registry.register(
        ToolDefinition(
            name="echo",
            description="echo",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=cancelling_echo,
        )
    )
    runner = ToolRunner(registry, session_factory=None)
    llm_tools = make_llm_tools(registry.list_enabled(_permissions_stub()))
    service = _service(fake_stream)

    result = await service._stream_loop(
        _prepared_stub(),
        FakeStreamer(),
        llm_tools=llm_tools,
        tool_runner=runner,
        user=SimpleNamespace(id=uuid.uuid4()),
        permissions=_permissions_stub(),
        cancellation=cancellation,
    )

    assert result is not None
    outcome, tool_records, _attempts, _attempt_ids = result
    assert outcome.cancelled is True
    assert outcome.usage is not None  # известный usage НЕ теряется (§2)
    assert outcome.usage == Usage(
        input_tokens=100, output_tokens=20, reasoning_tokens=5, total_tokens=125
    )
    assert len(tool_records) == 1
    assert len(calls) == 1


# --- _save_cancelled: usage обязан попасть в generation_runs (§2) ---------------


class _FakeSession:
    async def commit(self) -> None:
        pass


class _FakeSessionFactory:
    """async_sessionmaker-совместимый фейк: вызов → async CM, yielding session."""

    def __call__(self) -> _FakeSessionFactory:
        return self

    async def __aenter__(self) -> _FakeSession:
        return _FakeSession()

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeMessageRepository:
    calls: list[dict[str, Any]] = []

    def __init__(self, session: _FakeSession) -> None:
        pass

    async def add_message(self, chat_id: Any, role: str, **kw: Any) -> None:
        type(self).calls.append(kw)


class _FakeChatRepository:
    def __init__(self, session: _FakeSession) -> None:
        pass

    async def touch(self, chat_id: Any) -> None:
        pass


class _FakeGenerationRunRepository:
    finish_calls: list[dict[str, Any]] = []

    def __init__(self, session: _FakeSession) -> None:
        pass

    async def finish(self, run_id: Any, **kw: Any) -> None:
        type(self).finish_calls.append(kw)


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append(text)


async def test_save_cancelled_persists_known_usage_to_run(monkeypatch: Any) -> None:
    """(бывший xfail-blocker, исправлено в FIX V2) Cancelled run с известным usage
    → finish() получает input/output/reasoning."""
    """Cancelled run с известным usage → finish() получает input/output/reasoning."""
    _FakeMessageRepository.calls = []
    _FakeGenerationRunRepository.finish_calls = []
    monkeypatch.setattr(generation_module, "MessageRepository", _FakeMessageRepository)
    monkeypatch.setattr(generation_module, "ChatRepository", _FakeChatRepository)
    monkeypatch.setattr(generation_module, "GenerationRunRepository", _FakeGenerationRunRepository)

    service = GenerationService(
        session_factory=_FakeSessionFactory(),
        registry=default_registry(),
        llm_stream=_empty_stream,
        settings=_SETTINGS,
        generation_registry=GenerationRegistry(),
    )
    outcome = StreamOutcome(
        text="часть ответа",
        usage=Usage(input_tokens=100, output_tokens=20, reasoning_tokens=5, total_tokens=125),
        cancelled=True,
        tool_calls_count=1,
        first_token_at=None,
        reasoning_chunks=1,
    )
    await service._save_cancelled(_prepared_stub(), outcome, bot=_FakeBot(), tg_chat_id=1)

    add_message_calls = _FakeMessageRepository.calls
    finish_calls = _FakeGenerationRunRepository.finish_calls
    # Assistant message usage уже сохраняет — это работает и сегодня.
    assert add_message_calls and add_message_calls[0]["input_tokens"] == 100
    assert add_message_calls[0]["output_tokens"] == 20
    # Контракт §2: generation_runs (usage ledger) тоже обязан получить usage.
    assert finish_calls and finish_calls[0]["status"] == "cancelled"
    assert finish_calls[0].get("input_tokens") == 100
    assert finish_calls[0].get("output_tokens") == 20
    assert finish_calls[0].get("reasoning_tokens") == 5
