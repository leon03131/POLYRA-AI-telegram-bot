"""Глобальный обработчик необработанных исключений хендлеров."""

import logging
from contextlib import suppress

from aiogram.types import ErrorEvent

logger = logging.getLogger(__name__)

_USER_ERROR_TEXT = "⚠️ Произошла внутренняя ошибка. Попробуйте позже."


async def on_error(event: ErrorEvent) -> bool:
    """Залогировать исключение и мягко ответить пользователю (без техдеталей).

    Возвращает True — ошибка считается обработанной.
    """
    logger.error(
        "unhandled error while processing update %s",
        event.update.update_id,
        exc_info=event.exception,
    )
    # Ответ пользователю — best effort: апдейт может не содержать чата для ответа.
    with suppress(Exception):
        if event.update.message is not None:
            await event.update.message.answer(_USER_ERROR_TEXT)
        elif event.update.callback_query is not None:
            await event.update.callback_query.answer(_USER_ERROR_TEXT, show_alert=True)
    return True
