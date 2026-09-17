"""Остановка генерации (Bot API 10.3, update stopped_message_generation)."""

import logging

from aiogram import Router
from aiogram.types import MessageGenerationStopped

logger = logging.getLogger(__name__)

router = Router(name="stop")


@router.stopped_message_generation()
async def on_generation_stopped(event: MessageGenerationStopped) -> None:
    # TODO(M5): реальная отмена генерации по (chat_id, draft_id).
    logger.info("generation stop requested chat=%s draft=%s", event.chat.id, event.draft_id)
