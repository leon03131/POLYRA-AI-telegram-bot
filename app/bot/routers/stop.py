"""Остановка генерации (Bot API 10.3, update stopped_message_generation)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.types import MessageGenerationStopped

if TYPE_CHECKING:
    from app.services.generation import GenerationRegistry

logger = logging.getLogger(__name__)

router = Router(name="stop")


@router.stopped_message_generation()
async def on_generation_stopped(
    event: MessageGenerationStopped,
    generation_registry: GenerationRegistry,
) -> None:
    """Отменить активную генерацию по (chat_id, draft_id); unknown — только лог."""
    stopped = await generation_registry.stop(event.chat.id, event.draft_id)
    logger.info(
        "generation stop: chat=%s draft=%s found=%s",
        event.chat.id,
        event.draft_id,
        stopped,
    )
