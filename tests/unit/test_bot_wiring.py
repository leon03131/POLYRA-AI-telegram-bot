"""Тесты wiring генерации в bot-слой (M5). Без Telegram API, БД и сети."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import cast

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.dispatcher import create_dispatcher
from app.bot.routers.stop import on_generation_stopped
from app.config import Settings
from app.services.generation import ActiveGeneration, GenerationRegistry


def test_dispatcher_exposes_generation_objects_in_workflow_data() -> None:
    """generation_service/generation_registry попадают в dp.workflow_data (DI по имени)."""
    settings = Settings()
    session_factory = async_sessionmaker()
    generation_service = SimpleNamespace(name="generation_service")
    generation_registry = SimpleNamespace(name="generation_registry")

    dp = create_dispatcher(
        settings,
        session_factory,
        generation_service=cast("object", generation_service),
        generation_registry=cast("object", generation_registry),
    )

    assert dp["settings"] is settings
    assert dp["generation_service"] is generation_service
    assert dp["generation_registry"] is generation_registry


def _register_active(
    registry: GenerationRegistry, tg_chat_id: int, draft_id: int, cancel: asyncio.Event
) -> asyncio.Task[None]:
    """Зарегистрировать реальную ActiveGeneration с фиктивной задачей."""

    async def _never() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(_never())
    registry.register(
        ActiveGeneration(
            task=task,
            cancellation=cancel,
            draft_id=draft_id,
            tg_chat_id=tg_chat_id,
            chat_id=uuid.uuid4(),
        )
    )
    return task


async def test_stop_handler_cancels_registered_generation() -> None:
    """stopped_message_generation выставляет cancellation активной генерации."""
    registry = GenerationRegistry()
    cancel = asyncio.Event()
    task = _register_active(registry, tg_chat_id=1, draft_id=2, cancel=cancel)

    event = SimpleNamespace(chat=SimpleNamespace(id=1), draft_id=2)
    await on_generation_stopped(event, registry)

    assert cancel.is_set()
    task.cancel()


async def test_stop_handler_unknown_generation_is_noop() -> None:
    """Stop по неизвестному (chat_id, draft_id) не падает; stop() возвращает False."""
    registry = GenerationRegistry()
    event = SimpleNamespace(chat=SimpleNamespace(id=10), draft_id=20)

    await on_generation_stopped(event, registry)
    assert await registry.stop(10, 20) is False
